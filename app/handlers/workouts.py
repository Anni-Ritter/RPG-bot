from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.keyboards import bonus_finish_menu
from app.models import WorkoutSession
from app.services.achievements import achievement_messages, check_achievements
from app.services.assets import send_reaction
from app.services.phrases import BONUS_ACCEPTED, BONUS_DECLINED, pick
from app.services.rewards import apply_reward, get_or_create_profile

router = Router()
TZ = ZoneInfo(settings.timezone)


def now_local() -> datetime:
    return datetime.now(TZ)


async def _get_today_session(session, user_id: int, kind: str):
    return await session.scalar(
        select(WorkoutSession).where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.workout_date == now_local().date(),
            WorkoutSession.kind == kind,
        )
    )


@router.callback_query(F.data.startswith("workout:start:"))
async def start_workout(callback: CallbackQuery) -> None:
    kind = callback.data.rsplit(":", 1)[1]
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        workout = await _get_today_session(session, profile.id, kind)
        if workout and workout.status == "completed":
            await callback.answer("Эта тренировка уже закрыта.", show_alert=True)
            return
        if not workout:
            workout = WorkoutSession(user_id=profile.id, workout_date=now_local().date(), kind=kind)
            session.add(workout)
        workout.status = "in_progress"
        workout.started_at = workout.started_at or now_local()
        await session.commit()
    await send_reaction(callback.message, "selin", "beckoning", "Селин: — Вот и хорошо. Не заставляй меня жалеть, что я на тебя рассчитывала.")
    await callback.answer()


@router.callback_query(F.data.startswith("workout:skip:"))
async def skip_workout(callback: CallbackQuery) -> None:
    kind = callback.data.rsplit(":", 1)[1]
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        workout = await _get_today_session(session, profile.id, kind)
        if workout and workout.status == "completed":
            await callback.answer("Она уже закрыта как выполненная.", show_alert=True)
            return
        if not workout:
            workout = WorkoutSession(user_id=profile.id, workout_date=now_local().date(), kind=kind)
            session.add(workout)
        workout.status = "skipped"
        await session.commit()
    await send_reaction(callback.message, "selin", "neutral", "Селин: — Принято. Сегодня без этого.")
    await callback.answer()


@router.callback_query(F.data == "workout:finish:monday")
async def finish_monday(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        workout = await _get_today_session(session, profile.id, "monday")
        if not workout or workout.status != "in_progress":
            await callback.answer("Активной тренировки нет или она уже закрыта.", show_alert=True)
            return
        workout.status = "completed"
        workout.completed_at = now_local()
        await apply_reward(session, profile, event_type="monday_workout_complete", xp=100, coins=20, strength=1)
        unlocked = await check_achievements(session, profile)
        await session.commit()
    await send_reaction(callback.message, "selin", "triumphant", "Селин: — Хорошо. Сегодня к тебе вопросов нет.\n+100 XP · +20 монет · +1 Сила")
    for msg in achievement_messages(unlocked):
        await callback.message.answer(msg)
    await callback.answer()


@router.callback_query(F.data == "workout:later:monday")
async def monday_later(callback: CallbackQuery) -> None:
    await send_reaction(callback.message, "selin", "smirk", "Селин: — Ладно. Тогда закончишь — вернись к этой кнопке.")
    await callback.answer()


async def _complete_thursday_first(session, profile) -> bool:
    first = await _get_today_session(session, profile.id, "thursday_first")
    if not first or first.status != "in_progress":
        return False
    first.status = "completed"
    first.completed_at = now_local()
    await apply_reward(session, profile, event_type="thursday_first_complete", xp=100, coins=20, strength=1)
    return True


@router.callback_query(F.data == "workout:bonus:leave")
async def leave_bonus(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        completed = await _complete_thursday_first(session, profile)
        if not completed:
            await callback.answer("Первая тренировка уже закрыта или не была начата.", show_alert=True)
            return
        unlocked = await check_achievements(session, profile)
        await session.commit()
    await send_reaction(callback.message, "selin", "neutral", pick(BONUS_DECLINED) + "\n+100 XP · +20 монет · +1 Сила")
    for msg in achievement_messages(unlocked):
        await callback.message.answer(msg)
    await callback.answer()


@router.callback_query(F.data == "workout:bonus:go")
async def go_bonus(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        completed = await _complete_thursday_first(session, profile)
        if not completed:
            await callback.answer("Первая тренировка уже закрыта или не была начата.", show_alert=True)
            return
        bonus = await _get_today_session(session, profile.id, "thursday_bonus")
        if not bonus:
            bonus = WorkoutSession(user_id=profile.id, workout_date=now_local().date(), kind="thursday_bonus")
            session.add(bonus)
        bonus.status = "in_progress"
        bonus.started_at = bonus.started_at or now_local()
        await session.commit()
    await send_reaction(
        callback.message, "tori", "treasure",
        pick(BONUS_ACCEPTED) + "\n\nПервая тренировка закрыта: +100 XP · +20 монет · +1 Сила",
        reply_markup=bonus_finish_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "workout:finish:thursday_bonus")
async def finish_bonus(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        bonus = await _get_today_session(session, profile.id, "thursday_bonus")
        if not bonus or bonus.status != "in_progress":
            await callback.answer("Активной второй тренировки нет или она уже закрыта.", show_alert=True)
            return
        bonus.status = "completed"
        bonus.completed_at = now_local()
        profile.trial_chests += 1
        await apply_reward(
            session,
            profile,
            event_type="thursday_bonus_complete",
            xp=150,
            coins=40,
            willpower=1,
            bond=5,
            payload={"trial_chest_delta": 1},
        )
        unlocked = await check_achievements(session, profile)
        await session.commit()
    await send_reaction(
        callback.message, "selin", "soft_smile",
        "Селин: — Вот теперь это уже похоже не на случайную удачу, а на характер.\n"
        "+150 XP · +40 монет · +1 Воля · +5 связь с Тори\n"
        "🎁 Получен Сундук испытания."
    )
    for msg in achievement_messages(unlocked):
        await callback.message.answer(msg)
    await callback.answer()
