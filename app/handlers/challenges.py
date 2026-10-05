from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.db import SessionLocal
from app.keyboards import daily_challenge_active_menu, daily_challenge_options_menu
from app.models import DailyChallengePlan
from app.services.assets import send_reaction
from app.services.challenges import (
    challenge_summary,
    challenge_value,
    ensure_plan,
    evaluate_plan,
    progress_text,
    select_option,
)
from app.services.rewards import get_or_create_profile

router = Router()


def _options_text(plan: DailyChallengePlan) -> str:
    lines = [
        "Выбери один челлендж на сегодня. Провал ничего не отнимает — просто не будет награды.",
        f"Награда: +{plan.reward_xp} XP · +{plan.reward_coins} монет. Каждый третий выполненный челлендж даёт сундук.",
        "",
    ]
    for i, option in enumerate(list(plan.options or [])[:3], start=1):
        lines.append(f"{i}. {option.get('title', 'Челлендж')}")
        lines.append(str(option.get("description") or ""))
        if option.get("why"):
            lines.append(f"Зачем: {option['why']}")
        lines.append("")
    return "\n".join(lines).strip()


async def _show_challenge(message: Message, telegram_id: int, full_name: str | None) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, telegram_id, full_name)
        plan = await ensure_plan(session, profile)
        if plan.status == "active":
            status, current, chest = await evaluate_plan(session, profile, plan, final=False)
            await session.commit()
        else:
            status, current, chest = plan.status, 0, False

        if status == "choosing":
            text = _options_text(plan)
            markup = daily_challenge_options_menu(plan.id, list(plan.options or []))
            emotion = "curious"
        elif status == "active":
            selected = dict(plan.selected or {})
            text = (
                f"🎯 {selected.get('title', 'Челлендж дня')}\n"
                f"{selected.get('description', '')}\n\n"
                f"Прогресс: {progress_text(str(selected.get('code') or ''), current, int(selected.get('target') or 0))}\n"
                f"Награда: +{plan.reward_xp} XP · +{plan.reward_coins} монет"
            )
            markup = daily_challenge_active_menu(plan.id)
            emotion = "smirk"
        elif status == "completed":
            text = (
                "🎯 Челлендж дня выполнен.\n"
                + challenge_summary(plan, current, final=True)
                + f"\n\n+{plan.reward_xp} XP · +{plan.reward_coins} монет"
            )
            if chest:
                text += "\n✨ Это третий выполненный челлендж — +1 Сундук испытания."
            markup = None
            emotion = "triumphant"
        else:
            text = "Сегодняшний челлендж не закрыт. Ничего не потеряно — завтра будет новый выбор."
            markup = None
            emotion = "neutral"

    await send_reaction(message, "selin", emotion, text, reply_markup=markup)


@router.message(F.text == "🎯 Челлендж")
async def daily_challenge(message: Message) -> None:
    await _show_challenge(message, message.from_user.id, message.from_user.full_name)


@router.callback_query(F.data == "challenge:open")
async def open_challenge_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _show_challenge(callback.message, callback.from_user.id, callback.from_user.full_name)


@router.callback_query(F.data.startswith("challenge:select:"))
async def choose_challenge(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        return
    plan_id = int(parts[2])
    index = int(parts[3])

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        plan = await session.get(DailyChallengePlan, plan_id)
        if not plan or plan.user_id != profile.id:
            await callback.answer("Этот челлендж не найден.", show_alert=True)
            return
        if not await select_option(session, plan, index):
            await callback.answer("Челлендж на сегодня уже выбран.", show_alert=True)
            return
        selected = dict(plan.selected or {})
        current = await challenge_value(session, profile, plan)
        await session.commit()

    await callback.answer("Выбрано")
    await send_reaction(
        callback.message,
        "selin",
        "smirk",
        (
            f"— Хорошо. На сегодня: {selected.get('title', 'челлендж')}.\n\n"
            f"{selected.get('description', '')}\n"
            f"Сейчас: {progress_text(str(selected.get('code') or ''), current, int(selected.get('target') or 0))}\n\n"
            "Если закроешь — забираешь награду. Если нет, ничего не отнимаю."
        ),
        reply_markup=daily_challenge_active_menu(plan_id),
    )


@router.callback_query(F.data.startswith("challenge:check:"))
async def check_challenge(callback: CallbackQuery) -> None:
    plan_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        plan = await session.get(DailyChallengePlan, plan_id)
        if not plan or plan.user_id != profile.id:
            await callback.answer("Челлендж не найден.", show_alert=True)
            return
        status, current, chest = await evaluate_plan(session, profile, plan, final=False)
        selected = dict(plan.selected or {})
        await session.commit()

    await callback.answer()
    if status == "completed":
        text = f"— Есть. Челлендж закрыт.\n\n+{plan.reward_xp} XP · +{plan.reward_coins} монет"
        if chest:
            text += "\n✨ И ещё +1 Сундук испытания за три закрытых челленджа."
        await send_reaction(callback.message, "selin", "triumphant", text)
        return

    if status == "active":
        await send_reaction(
            callback.message,
            "selin",
            "curious",
            f"Пока не всё.\n{progress_text(str(selected.get('code') or ''), current, int(selected.get('target') or 0))}",
            reply_markup=daily_challenge_active_menu(plan_id),
        )
        return

    await send_reaction(callback.message, "selin", "neutral", "На сегодня этот челлендж уже закрыт.")
