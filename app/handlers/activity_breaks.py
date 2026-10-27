"""Opt-in real-life activity break commands, callbacks and natural-language prompt."""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db import SessionLocal
from app.services.activity_breaks import (
    BreakProposal, adapt_today_challenges, extend_break, get_break, is_in_effect,
    now_local, parse_break_proposal, reason_label, resume_break, start_break,
)
from app.services.rewards import get_or_create_profile

router = Router()


def _proposal_markup(telegram_id: int, proposal: BreakProposal) -> InlineKeyboardMarkup:
    today = now_local().date()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Да, поставить на паузу",
            callback_data=f"activity:yes:{telegram_id}:{proposal.days}:{proposal.reason}:{today:%Y%m%d}",
        )],
        [InlineKeyboardButton(text="Нет, просто рассказываю", callback_data=f"activity:no:{telegram_id}")],
    ])


async def offer_break(message: Message, proposal: BreakProposal) -> None:
    if not message.from_user:
        return
    check_on = now_local().date() + timedelta(days=proposal.days)
    await message.answer(
        "Селин: — Поняла, ты говоришь об ограничении нагрузок "
        f"{reason_label(proposal.reason)} примерно на {proposal.days} дн. "
        f"Поставить спортивные напоминания и челленджи на паузу до {check_on:%d.%m.%Y}? "
        "Остальные функции останутся доступны.",
        reply_markup=_proposal_markup(message.from_user.id, proposal),
    )


class ActivityBreakMiddleware(BaseMiddleware):
    async def __call__(
        self, handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message, data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user and event.text:
            proposal = parse_break_proposal(event.text)
            if proposal:
                await offer_break(event, proposal)
                return None
        return await handler(event, data)


@router.message(Command("activity_pause"))
async def activity_pause_command(message: Message) -> None:
    words = (message.text or "").split(maxsplit=2)
    if len(words) < 2 or not words[1].isdigit() or not 1 <= int(words[1]) <= 90:
        await message.answer(
            "Чтобы сделать перерыв: /activity_pause 14\n"
            "Число — количество дней (от 1 до 90). Бот сначала попросит подтверждение."
        )
        return
    proposal = BreakProposal(
        days=int(words[1]),
        reason="tattoo" if len(words) > 2 and "тату" in words[2].lower() else "rest",
    )
    await offer_break(message, proposal)


@router.message(Command("activity_status"))
async def activity_status_command(message: Message) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        row = await get_break(session, profile.id)
        await session.commit()
    if is_in_effect(row, now_local().date()):
        suffix = (
            "Я уже спросила о возвращении и пока жду твоего решения."
            if row.status == "awaiting_confirmation" else
            f"Проверим возможность возвращения {row.check_on:%d.%m.%Y}."
        )
        await message.answer(
            f"🌿 Спортивная пауза активна {reason_label(row.reason)}. {suffix}\n"
            "Команды: /activity_resume или /activity_extend 7"
        )
    else:
        await message.answer("Сейчас спортивная пауза не установлена. /activity_pause 14 — установить.")


@router.message(Command("activity_resume"))
async def activity_resume_command(message: Message) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        changed = await resume_break(session, profile.id)
        await session.commit()
    await message.answer(
        "Селин: — Спортивные напоминания снова включены. Вернёмся постепенно, в пределах разрешённых нагрузок."
        if changed else "Спортивная пауза уже выключена."
    )


@router.message(Command("activity_extend"))
async def activity_extend_command(message: Message) -> None:
    words = (message.text or "").split(maxsplit=1)
    days = int(words[1]) if len(words) > 1 and words[1].isdigit() else 7
    if not 1 <= days <= 90:
        await message.answer("Продление — от 1 до 90 дней. Например: /activity_extend 7")
        return
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        row = await extend_break(session, profile.id, days)
        await session.commit()
    await message.answer(
        f"🌿 Пауза продлена. Проверим снова {row.check_on:%d.%m.%Y}."
        if row else "Активной паузы нет. Чтобы начать: /activity_pause 14"
    )


@router.callback_query(F.data.startswith("activity:"))
async def activity_callback(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 3 or not parts[2].isdigit() or int(parts[2]) != callback.from_user.id:
        await callback.answer("Эта кнопка не для тебя.", show_alert=True)
        return
    kind = parts[1]
    if kind == "no":
        await callback.answer()
        await callback.message.answer("Селин: — Хорошо, ничего не меняю.")
        return

    if kind == "yes":
        if (len(parts) != 6 or not parts[3].isdigit() or parts[4] not in {"tattoo", "rest"}
                or not 1 <= int(parts[3]) <= 90 or parts[5] != f"{now_local().date():%Y%m%d}"):
            await callback.answer("Предложение устарело. Напиши /activity_pause 14 заново.", show_alert=True)
            return
        days, reason = int(parts[3]), parts[4]
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
            row = await start_break(session, profile.id, days, reason)
            replaced = await adapt_today_challenges(session, profile.id, now_local().date())
            await session.commit()
            check_on = row.check_on
        await callback.answer("Спортивная пауза включена")
        await callback.message.answer(
            "Селин: — Договорились. До " + check_on.strftime("%d.%m.%Y") +
            " я не напоминаю о тренировках и не даю спортивных испытаний. "
            "Сюжет, еда и Тори продолжаются как обычно. "
            + ("Сегодняшние незавершённые спортивные челленджи заменены без штрафа. " if replaced else "")
            + "Возвращение — только после твоего подтверждения.\n"
            "Проверить: /activity_status · Продлить: /activity_extend 7 · Вернуться: /activity_resume"
        )
        return

    if kind in {"extend", "resume"} and len(parts) == 4:
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
            row = await get_break(session, profile.id)
            if not row or row.status == "resumed" or parts[3] != row.check_on.strftime("%Y%m%d"):
                await callback.answer("Это предложение уже неактуально.", show_alert=True)
                return
            if kind == "extend":
                row = await extend_break(session, profile.id, 7)
                text = f"Селин: — Ещё семь дней без спортивного давления. Проверим {row.check_on:%d.%m.%Y}."
            else:
                await resume_break(session, profile.id)
                text = "Селин: — Хорошо, включаю тренировки снова. Только без резкого старта."
            await session.commit()
        await callback.answer()
        await callback.message.answer(text)
        return
    await callback.answer("Неизвестная команда.", show_alert=True)
