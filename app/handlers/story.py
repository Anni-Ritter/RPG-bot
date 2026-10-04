from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.db import SessionLocal
from app.keyboards import story_choices_menu
from app.services.assets import send_background, story_background_key
from app.services.rewards import get_or_create_profile
from app.services.scrolls import grant_scroll
from app.services.story import (
    STORY_REWARDS,
    choice_key,
    get_or_create_story_progress,
    get_scene,
    reward_flag,
    story_day,
)

router = Router()
TZ = ZoneInfo(settings.timezone)


@router.message(F.text == "📖 История")
async def story_today(message: Message) -> None:
    today = datetime.now(TZ).date()
    reward_messages: list[str] = []

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        progress = await get_or_create_story_progress(session, profile.id, today)
        day = story_day(progress, today)
        scene = get_scene(day)
        flags = dict(progress.flags or {})

        first_view_today = day > progress.last_viewed_day
        if first_view_today:
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


@router.callback_query(F.data.startswith("storychoice:"))
async def story_choice(callback: CallbackQuery) -> None:
    _, day_raw, choice_id = callback.data.split(":", 2)
    day = int(day_raw)
    today = datetime.now(TZ).date()

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
