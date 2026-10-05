from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.db import SessionLocal
from app.keyboards import selin_chat_menu
from app.services.assets import send_reaction
from app.services.levels import level_from_xp
from app.services.rewards import get_or_create_profile
from app.services.story import (
    add_affinity,
    advance_week1_if_due,
    affinity_label,
    get_or_create_story_progress,
    get_story_flag,
    initialize_week1_v2,
    set_story_flag,
    story_day,
)

router = Router()
TZ = ZoneInfo(settings.timezone)


def _today():
    return datetime.now(TZ).date()


def _response(topic: str, day: int, affinity: int) -> tuple[str, str]:
    if topic == "self":
        if day <= 3:
            return (
                "thoughtful",
                "— О себе? Пока у меня очень короткий список. Селин. Умею обращаться с оружием. Не люблю неизвестность. Остальное, видимо, придётся выяснять вместе.",
            )
        if day <= 5:
            return (
                "curious",
                "— Я начинаю замечать странную вещь: некоторые навыки у меня есть, а воспоминаний о том, где я их получила, нет. Это раздражает сильнее, чем сама амнезия.",
            )
        return (
            "soft_smile" if affinity >= 4 else "neutral",
            "— Раньше я бы сказала, что сначала сама разберусь в себе, а потом что-нибудь расскажу. Сейчас это уже звучит глупо. Ты всё равно рядом с самого начала.",
        )

    if topic == "seal":
        if day <= 2:
            return (
                "judging",
                "— Пока я знаю только три вещи: Печать появилась не по моей воле, реагирует на тебя и очень любит создавать новые проблемы. Отличное начало.",
            )
        return (
            "thoughtful",
            "— Чем выше наша синхронизация, тем меньше Печать ведёт себя как сломанная сигнализация. Я уже могу иногда удерживать её сама. Мне всё ещё не нравится, что она вообще связана с тобой.",
        )

    if topic == "tori":
        if day < 2:
            return ("neutral", "— Кто такой Тори? Хороший вопрос. Пока никто. И мне нравится такой расклад.")
        if affinity >= 5:
            return (
                "smirk",
                "— Он ворует мои вещи, игнорирует слово «нельзя» и спит на моей одежде. И нет, я его не выгоню. Даже не начинай.",
            )
        return (
            "judging",
            "— Я всё ещё утверждаю, что он просто увязался за нами. Тори с этой версией явно не согласен.",
        )

    if topic == "how":
        if day <= 2:
            return (
                "neutral",
                "— Нормально. Насколько вообще можно быть нормально, когда просыпаешься в руинах и обнаруживаешь голос в голове. Но спасибо, что спросила.",
            )
        if affinity >= 4:
            return (
                "soft_smile",
                "— Сегодня лучше. И да, я понимаю, что это частично из-за тебя. Не жди, что я буду повторять это каждый день.",
            )
        return (
            "neutral",
            "— Я справляюсь. Если перестану — ты, скорее всего, узнаешь об этом раньше всех. У нашей связи есть сомнительные преимущества.",
        )

    # sit
    if affinity >= 6:
        return (
            "soft_smile",
            "Селин ничего не спрашивает и не пытается заполнить паузу. Через некоторое время она говорит:\n— Знаешь, к этому я уже привыкла. К тому, что ты просто есть рядом.",
        )
    return (
        "neutral",
        "— Можешь остаться. Не обязательно постоянно что-то решать.\n\nСелин занимается своими делами и впервые не выглядит так, будто твоё присутствие её напрягает.",
    )


@router.message(F.text == "💬 Селин")
async def selin_chat(message: Message) -> None:
    today = _today()
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        progress = await get_or_create_story_progress(session, profile.id, today)
        initialize_week1_v2(progress, profile, today)
        advance_week1_if_due(progress, today)
        day = story_day(progress, today)
        level, _, next_threshold = level_from_xp(profile.xp)
        relation = affinity_label(progress)
        await session.commit()

    if level < 2:
        await send_reaction(
            message,
            "selin",
            "neutral",
            f"Связь пока слишком слабая для нормального разговора.\n\nСинхронизация: {profile.xp} / {next_threshold or 250} XP\nНа уровне 2 разговоры с Селин откроются полностью.",
        )
        return

    await send_reaction(
        message,
        "selin",
        "curious",
        f"Селин: — Ну? Раз уж связь сегодня работает нормально, спрашивай.\n\nСейчас она {relation}.",
        reply_markup=selin_chat_menu(include_tori=day >= 2),
    )


@router.callback_query(F.data.startswith("selinchat:"))
async def selin_chat_topic(callback: CallbackQuery) -> None:
    topic = callback.data.split(":", 1)[1]
    if topic not in {"self", "seal", "how", "tori", "sit"}:
        await callback.answer("Такой темы нет.", show_alert=True)
        return

    today = _today()
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        progress = await get_or_create_story_progress(session, profile.id, today)
        initialize_week1_v2(progress, profile, today)
        advance_week1_if_due(progress, today)
        level, _, _ = level_from_xp(profile.xp)
        if level < 2:
            await callback.answer("Разговоры откроются на уровне синхронизации 2.", show_alert=True)
            return

        day = story_day(progress, today)
        affinity = int(get_story_flag(progress, "v2_affinity", 0))
        emotion, text = _response(topic, day, affinity)

        # Conversations should build attachment, but not be an affinity farm.
        reward_key = f"v2_chat_affinity_{today.isoformat()}"
        if not get_story_flag(progress, reward_key):
            add_affinity(progress, 1)
            set_story_flag(progress, reward_key, True)

        # Remember topics so later content can refer to what the player asked.
        seen_key = "v2_chat_topics_seen"
        seen = list(get_story_flag(progress, seen_key, []))
        if topic not in seen:
            seen.append(topic)
            set_story_flag(progress, seen_key, seen)

        await session.commit()

    await send_reaction(callback.message, "selin", emotion, text)
    await callback.answer()
