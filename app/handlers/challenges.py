from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from app.db import SessionLocal
from app.keyboards import daily_challenge_options_menu
from app.models import DailyChallengePlan
from app.services.challenges import (
    active_indices,
    available_indices,
    challenge_state,
    challenge_value_for_option,
    ensure_plan,
    evaluate_plan,
    get_plan,
    now_local,
    progress_text,
    select_option,
)
from app.services.challenge_feedback import check_and_notify_challenges
from app.services.rewards import get_or_create_profile

router = Router()

# One bot process is enough on Bothost. This lock makes repeated taps harmless while
# the OpenAI request is still running and the user is waiting for the three options.
_generation_locks: set[int] = set()
_action_locks: dict[int, asyncio.Lock] = {}


def _user_lock(telegram_id: int) -> asyncio.Lock:
    lock = _action_locks.get(telegram_id)
    if lock is None:
        lock = asyncio.Lock()
        _action_locks[telegram_id] = lock
    return lock


def _trim(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def _plan_text_and_markup(session, profile, plan: DailyChallengePlan):
    state = challenge_state(plan)
    values = {}
    options = list(plan.options or [])[:3]

    lines = [
        "🎯 Испытания на сегодня",
        "Можно взять один, два или все три — хоть по очереди.",
        f"Каждый закрытый: +{plan.reward_xp} XP · +{plan.reward_coins} монет. Каждый третий закрытый даёт сундук.",
        "",
    ]

    for index, option in enumerate(options):
        if index in state["completed"]:
            icon = "✅"
        elif index in state["failed"]:
            icon = "❌"
        elif index in state["accepted"]:
            icon = "🎯"
        else:
            icon = "➕"

        lines.append(f"{icon} {index + 1}. {_trim(option.get('title', 'Челлендж'), 45)}")
        lines.append(_trim(option.get("description", ""), 145))
        if index in state["accepted"]:
            current = values.get(index)
            if current is None:
                current = await challenge_value_for_option(session, profile, plan, option)
            lines.append(
                "Прогресс: "
                + progress_text(
                    str(option.get("code") or ""),
                    current,
                    int(option.get("target") or 0),
                )
            )
        elif option.get("why"):
            lines.append("Зачем: " + _trim(option.get("why"), 95))
        lines.append("")

    markup = daily_challenge_options_menu(
        options,
        accepted=state["accepted"],
        completed=state["completed"],
        failed=state["failed"],
    )
    return "\n".join(lines).strip(), markup


async def _edit_challenge_message(message: Message, text: str, reply_markup=None) -> None:
    """Prefer replacing the existing challenge message instead of adding chat clutter."""
    try:
        if message.photo:
            # Telegram photo caption limit is 1024 chars.
            caption = text if len(text) <= 1000 else text[:997].rstrip() + "…"
            await message.edit_caption(caption=caption, reply_markup=reply_markup)
        else:
            await message.edit_text(text, reply_markup=reply_markup)
        return
    except TelegramBadRequest:
        pass
    except Exception as exc:
        print("Challenge message edit error:", repr(exc))

    await message.answer(text, reply_markup=reply_markup)


async def _show_challenge(message: Message, telegram_id: int, full_name: str | None) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, telegram_id, full_name)
        plan = await ensure_plan(session, profile)
        text, markup = await _plan_text_and_markup(session, profile, plan)
        await session.commit()
    await _edit_challenge_message(message, text, markup)


@router.message(F.text == "🎯 Челлендж")
async def daily_challenge(message: Message) -> None:
    telegram_id = message.from_user.id
    if telegram_id in _generation_locks:
        # First tap already produced the visible loading message. Ignore button spam.
        return

    _generation_locks.add(telegram_id)
    loading = await message.answer("⏳ Селин подбирает три испытания на сегодня…")
    try:
        await _show_challenge(loading, telegram_id, message.from_user.full_name)
    finally:
        _generation_locks.discard(telegram_id)


@router.callback_query(F.data == "challenge:open")
async def open_challenge_callback(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    if telegram_id in _generation_locks:
        await callback.answer("Уже подбираю. Секунду.")
        return

    _generation_locks.add(telegram_id)
    await callback.answer("Подбираю испытания…")
    # Immediate visible feedback and the old button disappears, so it cannot be mashed five times.
    await _edit_challenge_message(callback.message, "⏳ Селин подбирает три испытания на сегодня…", None)
    try:
        await _show_challenge(callback.message, telegram_id, callback.from_user.full_name)
    finally:
        _generation_locks.discard(telegram_id)


@router.callback_query(F.data == "challenge:noop")
async def challenge_noop(callback: CallbackQuery) -> None:
    await callback.answer("Этот пункт уже взят или закрыт.")


@router.callback_query(F.data.startswith("challenge:select:"))
async def choose_challenge(callback: CallbackQuery) -> None:
    # V12 callbacks are challenge:select:<index>. Old V9-V11 messages contain
    # challenge:select:<plan_id>:<index>; taking the last component keeps them usable.
    try:
        index = int(callback.data.rsplit(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer("Не смогла понять этот челлендж.", show_alert=True)
        return

    telegram_id = callback.from_user.id
    async with _user_lock(telegram_id):
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, telegram_id, callback.from_user.full_name)
            plan = await get_plan(session, profile.id, now_local().date())
            if plan is None:
                plan = await ensure_plan(session, profile)

            if not select_option(session, plan, index):
                await callback.answer("Этот челлендж уже у тебя.")
                text, markup = await _plan_text_and_markup(session, profile, plan)
                await session.commit()
                await _edit_challenge_message(callback.message, text, markup)
                return

            await session.commit()

        # If the user already met this target before accepting it, close it immediately
        # and announce the reward instead of silently turning the icon green.
        await check_and_notify_challenges(
            callback.message, telegram_id, callback.from_user.full_name
        )

        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, telegram_id, callback.from_user.full_name)
            plan = await get_plan(session, profile.id, now_local().date())
            text, markup = await _plan_text_and_markup(session, profile, plan)
            await session.commit()

    await callback.answer("Взято")
    await _edit_challenge_message(callback.message, text, markup)


@router.callback_query(F.data.startswith("challenge:check"))
async def check_challenge(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    async with _user_lock(telegram_id):
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, telegram_id, callback.from_user.full_name)
            plan = await get_plan(session, profile.id, now_local().date())
            if plan is None:
                await callback.answer("На сегодня испытаний ещё нет.", show_alert=True)
                return
            await session.commit()

        completed = await check_and_notify_challenges(
            callback.message, telegram_id, callback.from_user.full_name
        )

        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, telegram_id, callback.from_user.full_name)
            plan = await get_plan(session, profile.id, now_local().date())
            text, markup = await _plan_text_and_markup(session, profile, plan)
            await session.commit()

    await callback.answer("Испытание закрыто!" if completed else "Проверила прогресс.")
    await _edit_challenge_message(callback.message, text, markup)
