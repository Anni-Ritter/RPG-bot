from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.keyboards import story_choices_menu, story_v2_choices_menu
from app.services.assets import send_background, send_reaction, story_background_key
from app.services.levels import level_from_xp
from app.models import DailyStat, WorkoutSession
from app.services.rewards import get_or_create_profile
from app.services.scrolls import grant_scroll
from app.services.story import (
    STORY_REWARDS,
    add_affinity,
    advance_week1_if_due,
    begin_objective,
    choice_key,
    day_is_complete,
    get_or_create_story_progress,
    get_scene,
    get_story_flag,
    initialize_week1_v2,
    mark_day_complete,
    mark_objective_ready,
    objective_progress_text,
    objective_ready,
    reward_flag,
    set_story_flag,
    story_day,
    week1_phase,
)

router = Router()
TZ = ZoneInfo(settings.timezone)


def _now_date():
    return datetime.now(TZ).date()


def _day4_choices(profile) -> list[dict]:
    choices = []
    if profile.strength >= 1:
        choices.append({
            "id": "strength",
            "label": "💪 Использовать Силу",
            "response": "Селин не пытается аккуратно разбирать механизм, а просто фиксирует его в нужном положении и ломает заклинившую часть.\n\n— Хорошо. Вот это уже полезная характеристика.",
            "emotion": "smirk",
            "affinity": 0,
        })
    choices.append({
        "id": "careful",
        "label": "Разобраться аккуратно",
        "response": "Селин разбирается с механизмом вручную. Получается медленнее, но без проблем.\n\n— Работает. Просто хотелось бы понимать, почему я вообще знаю, что здесь делать.",
        "emotion": "thoughtful",
        "affinity": 0,
    })
    return choices


async def _day4_activity_already_done(session, user_id: int, today) -> bool:
    stat = await session.scalar(
        select(DailyStat).where(DailyStat.user_id == user_id, DailyStat.stat_date == today)
    )
    if stat and stat.steps >= 5000:
        return True

    workout = await session.scalar(
        select(WorkoutSession.id).where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.workout_date == today,
            WorkoutSession.status == "completed",
        ).limit(1)
    )
    return workout is not None


def _day7_choices(profile) -> list[dict]:
    level, _, _ = level_from_xp(profile.xp)
    choices = []
    if profile.strength >= 1:
        choices.append({
            "id": "strength",
            "label": "💪 Сила: сломать фиксатор Стража",
            "response": "Селин использует момент, когда Страж открывает защиту, и ломает один из его фиксаторов. После этого добить механизм уже несложно.\n\n— Очень тонкий подход. Зато быстрый.",
        })
    if profile.willpower >= 1:
        choices.append({
            "id": "will",
            "label": "🜂 Воля: удержать Печать",
            "response": "Страж пытается заставить Печать работать против Селин, но связь не срывается. Она удерживает знак под контролем и отключает защиту.\n\n— Вот для чего мне нужна была Воля. Теперь понятно.",
        })
    if profile.tori_bond >= 3:
        choices.append({
            "id": "tori",
            "label": "🦊 Довериться Тори",
            "response": "Тори обходит Стража сбоку и находит маленькую панель, которую Селин вообще не заметила. Через минуту механизм отключается.\n\n— Хорошо. Официально признаю: он полезный вор.",
        })
    if level >= 3:
        choices.append({
            "id": "sync",
            "label": "✦ Синхронизация: использовать Печать напрямую",
            "response": "Связь держится достаточно хорошо, чтобы Селин не просто реагировала на Печать, а сама направила её. Страж принимает команду и отключается.\n\n— Это уже начинает быть похоже на контроль, а не на случайность.",
        })
    choices.append({
        "id": "base",
        "label": "Обычный путь",
        "response": "Селин разбирается со Стражем без помощи характеристик. Дольше и неприятнее, но проход всё равно открывается.\n\n— Хорошо. Значит, даже без идеальной прокачки мы не застрянем.",
    })
    return choices


async def _show_week1(message: Message, profile, progress, day: int) -> None:
    scene = get_scene(day)
    phase = week1_phase(progress)
    header = f"День {day}/28 · Руины · {scene['title']}"

    if day_is_complete(progress, day):
        await send_background(
            message,
            story_background_key(day),
            f"{header}\n\nСегодняшняя часть уже завершена. Следующая сцена откроется завтра.",
        )
        return

    if phase == "objective":
        if objective_ready(profile, progress, day):
            mark_objective_ready(progress, day)
            mark_day_complete(progress, day, _now_date())
            await send_reaction(
                message,
                "selin" if day != 2 else "tori",
                scene.get("checkpoint_emotion", "neutral"),
                f"{scene['checkpoint']}\n\n✅ День завершён. Следующая сцена откроется завтра.",
            )
            return

        progress_line = objective_progress_text(profile, progress, day)
        await send_background(
            message,
            story_background_key(day),
            f"{header}\n\nТекущая задача:\n{scene['objective']}\n\n{progress_line}",
        )
        return

    if phase != "intro":
        await send_background(message, story_background_key(day), f"{header}\n\nВернись к текущей задаче.")
        return

    if day == 4:
        choices = _day4_choices(profile)
    elif day == 7:
        choices = _day7_choices(profile)
    else:
        choices = scene.get("choices", [])

    await send_background(
        message,
        story_background_key(day),
        f"{header}\n\n{scene['intro']}",
        reply_markup=story_v2_choices_menu(day, choices),
    )


@router.message(Command("restart_week1"))
async def restart_week1(message: Message) -> None:
    """Private playtest helper: reset story state, not player progression."""
    today = _now_date()
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        progress = await get_or_create_story_progress(session, profile.id, today)
        initialize_week1_v2(progress, profile, today, force=True)
        await session.commit()
    await message.answer("Первая неделя сюжета перезапущена. XP, монеты, характеристики и предметы не сбрасывались.")


@router.message(Command("next_story_day"))
async def next_story_day(message: Message) -> None:
    """Private playtest helper: unlock the next completed week-one day immediately."""
    today = _now_date()
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        progress = await get_or_create_story_progress(session, profile.id, today)
        initialize_week1_v2(progress, profile, today)
        day = story_day(progress, today)
        if day > 7:
            await message.answer("Первая тестовая неделя уже завершена.")
            return
        if not day_is_complete(progress, day):
            await message.answer("Сначала заверши текущий день сюжета.")
            return
        flags = dict(progress.flags or {})
        if day < 7:
            flags["v2_active_day"] = day + 1
            flags["v2_phase"] = "intro"
            flags["v2_day_unlocked_on"] = today.isoformat()
        else:
            flags["v2_active_day"] = 8
            flags["week1_v2_completed_on"] = today.isoformat()
        progress.flags = flags
        await session.commit()
    await message.answer("Тестовый следующий день открыт. Нажми 📖 История.")


@router.message(F.text == "📖 История")
async def story_today(message: Message) -> None:
    today = _now_date()
    reward_messages: list[str] = []

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        progress = await get_or_create_story_progress(session, profile.id, today)
        initialize_week1_v2(progress, profile, today)
        advance_week1_if_due(progress, today)
        day = story_day(progress, today)

        if day <= 7 and get_story_flag(progress, "week1_v2_initialized"):
            await _show_week1(message, profile, progress, day)
            await session.commit()
            return

        scene = get_scene(day)
        flags = dict(progress.flags or {})
        if day > progress.last_viewed_day:
            progress.last_viewed_day = day

        for scroll_id in STORY_REWARDS.get(day, []):
            key = reward_flag(day, scroll_id)
            if key in flags:
                continue
            result = await grant_scroll(session, profile, scroll_id)
            flags[key] = True
            if result["duplicate"]:
                reward_messages.append(
                    f"Сюжетная награда оказалась дубликатом и рассыпалась в Пыль ателье: +{result['dust']}."
                )
            else:
                reward_messages.append(f"🎴 Получен сюжетный свиток «{result['scroll'].name}».")

        progress.flags = flags
        await session.commit()

    chapter_names = {1: "Руины", 2: "Лес", 3: "Таррен", 4: "Шпиль"}
    header = f"День {day}/28 · {chapter_names[scene['chapter']]}"
    await send_background(
        message,
        story_background_key(day),
        f"{header}\n\n{scene['text']}",
        reply_markup=story_choices_menu(day, scene.get("choices", [])),
    )
    for reward in reward_messages:
        await message.answer(reward)


@router.callback_query(F.data.startswith("storyv2:"))
async def story_v2_choice(callback: CallbackQuery) -> None:
    _, day_raw, choice_id = callback.data.split(":", 2)
    day = int(day_raw)
    today = _now_date()

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        progress = await get_or_create_story_progress(session, profile.id, today)
        initialize_week1_v2(progress, profile, today)
        advance_week1_if_due(progress, today)

        if story_day(progress, today) != day or week1_phase(progress) != "intro":
            await callback.answer("Эта сцена уже закрыта.", show_alert=True)
            return

        scene = get_scene(day)
        if day == 4:
            choices = _day4_choices(profile)
        elif day == 7:
            choices = _day7_choices(profile)
        else:
            choices = scene.get("choices", [])

        choice = next((item for item in choices if item["id"] == choice_id), None)
        if not choice:
            await callback.answer("Вариант больше недоступен.", show_alert=True)
            return

        set_story_flag(progress, f"v2_choice_{day}", choice_id)
        affinity = int(choice.get("affinity", 0))
        if affinity:
            add_affinity(progress, affinity)

        # Days 1-4 start a real-world objective after the conversation.
        if day in {1, 2, 3, 4}:
            begin_objective(progress, profile, day)
            ready_now = objective_ready(profile, progress, day)
            if day == 4 and await _day4_activity_already_done(session, profile.id, today):
                ready_now = True
            if ready_now:
                mark_objective_ready(progress, day)
                mark_day_complete(progress, day, today)

            await session.commit()
            text = choice["response"]
            if ready_now:
                text += f"\n\n{scene['checkpoint']}\n\n✅ День завершён. Следующая сцена откроется завтра."
            else:
                text += f"\n\n🎯 {scene['objective']}\n{objective_progress_text(profile, progress, day)}"
            await send_reaction(
                callback.message,
                "selin",
                choice.get("emotion", "neutral"),
                text,
            )
            await callback.answer()
            return

        if day in {5, 6}:
            extra = ""
            if day == 6 and profile.willpower >= 1:
                extra = (
                    "\n\nПечать на секунду начинает мешать разговору, но связь не срывается. "
                    "Твоя Воля уже влияет на такие моменты — это не просто цифра в профиле."
                )
            mark_day_complete(progress, day, today)
            await session.commit()
            await send_reaction(
                callback.message,
                "selin",
                choice.get("emotion", "neutral"),
                choice["response"] + extra + "\n\n✅ День завершён. Следующая сцена откроется завтра.",
            )
            await callback.answer()
            return

        # Day 7 closes the first chapter and grants its guaranteed scroll.
        if day == 7:
            mark_day_complete(progress, day, today)
            result = await grant_scroll(session, profile, "s03")
            set_story_flag(progress, reward_flag(7, "s03"), True)
            await session.commit()

            reward = (
                f"Дубликат сюжетного свитка превратился в Пыль ателье: +{result['dust']}."
                if result["duplicate"]
                else f"🎴 Получен сюжетный свиток «{result['scroll'].name}»."
            )
            await send_background(
                callback.message,
                story_background_key(day),
                f"{choice['response']}\n\n{scene['complete']}\n\n{reward}",
            )
            await callback.answer()
            return


@router.callback_query(F.data.startswith("storychoice:"))
async def old_story_choice(callback: CallbackQuery) -> None:
    _, day_raw, choice_id = callback.data.split(":", 2)
    day = int(day_raw)
    today = _now_date()

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        progress = await get_or_create_story_progress(session, profile.id, today)
        scene = get_scene(day)

        if story_day(progress, today) != day:
            await callback.answer("Это решение уже осталось в прошлом.", show_alert=True)
            return

        key = choice_key(day)
        flags = dict(progress.flags or {})
        if key in flags:
            await callback.answer("Ты уже ответила на это.", show_alert=True)
            return

        choice = next((item for item in scene.get("choices", []) if item["id"] == choice_id), None)
        if not choice:
            await callback.answer("Вариант не найден.", show_alert=True)
            return

        flags[key] = choice_id
        progress.flags = flags
        await session.commit()

    await callback.message.answer(choice["response"])
    await callback.answer()
