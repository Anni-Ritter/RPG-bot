from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.db import SessionLocal
from app.keyboards import manual_workout_duration_menu, manual_workout_type_menu
from app.services.achievements import achievement_messages, check_achievements
from app.services.assets import send_background, send_reaction
from app.services.rewards import apply_reward, get_or_create_profile
from app.services.self_workouts import (
    WORKOUT_KIND_LABELS,
    manual_reward_already_claimed,
    mark_manual_reward_claimed,
    pick_reaction,
    reward_for_minutes,
)

router = Router()
TZ = ZoneInfo(settings.timezone)


class ManualWorkoutState(StatesGroup):
    waiting_minutes = State()


def now_local() -> datetime:
    return datetime.now(TZ)


@router.message(F.text == "🏋️ Тренировка")
async def manual_workout_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await send_background(
        message,
        "training_hall",
        "Что сегодня было? Запланированные тренировки понедельника и четверга бот отмечает отдельно — здесь можно записать любую дополнительную или домашнюю тренировку.",
        reply_markup=manual_workout_type_menu(),
    )


@router.callback_query(F.data.startswith("manualworkout:type:"))
async def manual_workout_type(callback: CallbackQuery, state: FSMContext) -> None:
    kind = callback.data.rsplit(":", 1)[1]
    if kind not in WORKOUT_KIND_LABELS:
        await callback.answer("Неизвестный тип тренировки.", show_alert=True)
        return

    await state.update_data(workout_kind=kind)
    await callback.message.answer(
        f"{WORKOUT_KIND_LABELS[kind]}. Сколько примерно занималась?",
        reply_markup=manual_workout_duration_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("manualworkout:minutes:"))
async def manual_workout_minutes(callback: CallbackQuery, state: FSMContext) -> None:
    raw = callback.data.rsplit(":", 1)[1]
    if raw == "custom":
        await state.set_state(ManualWorkoutState.waiting_minutes)
        await callback.message.answer("Введи количество минут числом, например 35.")
        await callback.answer()
        return

    try:
        minutes = int(raw)
    except ValueError:
        await callback.answer("Не получилось прочитать длительность.", show_alert=True)
        return

    await _save_manual_workout(callback.message, callback.from_user.id, callback.from_user.full_name, state, minutes)
    await callback.answer()


@router.message(ManualWorkoutState.waiting_minutes)
async def manual_workout_custom_minutes(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit():
        await message.answer("Нужно просто число минут, например 35.")
        return

    minutes = int(raw)
    if minutes <= 0 or minutes > 300:
        await message.answer("Введи реалистичное число от 1 до 300 минут.")
        return

    await _save_manual_workout(message, message.from_user.id, message.from_user.full_name, state, minutes)


async def _save_manual_workout(
    message: Message,
    telegram_id: int,
    full_name: str | None,
    state: FSMContext,
    minutes: int,
) -> None:
    if minutes <= 0 or minutes > 300:
        await message.answer("Введи реалистичное число от 1 до 300 минут.")
        return

    data = await state.get_data()
    kind = data.get("workout_kind")
    if kind not in WORKOUT_KIND_LABELS:
        await state.clear()
        await message.answer("Я потеряла тип тренировки. Нажми «🏋️ Тренировка» ещё раз.")
        return

    today = now_local().date()
    base_reward = reward_for_minutes(minutes)

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, telegram_id, full_name)
        already_rewarded = await manual_reward_already_claimed(session, profile.id, today)

        reward_allowed = not already_rewarded and base_reward.xp > 0
        xp = base_reward.xp if reward_allowed else 0
        coins = base_reward.coins if reward_allowed else 0

        if reward_allowed:
            await mark_manual_reward_claimed(session, profile.id, today)

        await apply_reward(
            session,
            profile,
            event_type="manual_workout_complete",
            xp=xp,
            coins=coins,
            payload={
                "kind": kind,
                "kind_label": WORKOUT_KIND_LABELS[kind],
                "minutes": minutes,
                "workout_date": today.isoformat(),
                "rewarded": reward_allowed,
            },
        )

        unlocked = await check_achievements(session, profile)
        await session.commit()

    character, emotion, reaction = pick_reaction(minutes)
    details = f"\n\n{WORKOUT_KIND_LABELS[kind]} · {minutes} мин."

    if reward_allowed:
        details += f"\n+{xp} XP · +{coins} монет"
    elif base_reward.xp == 0:
        details += "\nТренировка записана. Игровая награда начинается с 10 минут."
    else:
        details += "\nТренировка записана. Полная награда за самостоятельную тренировку сегодня уже получена."

    await send_reaction(message, character, emotion, reaction + details)
    for text in achievement_messages(unlocked):
        await message.answer(text)

    await state.clear()
