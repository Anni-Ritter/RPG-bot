"""Real Telegram confirmation buttons for actions proposed by Selin tools."""
from __future__ import annotations

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.db import SessionLocal
from app.services.rewards import get_or_create_profile
from app.services.selin_agent import execute_pending

router = Router()
log = logging.getLogger('selin.agent.callbacks')


def confirmation_keyboard(action_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Подтвердить', callback_data=f'agent:yes:{action_id}'),
         InlineKeyboardButton(text='Не надо', callback_data=f'agent:no:{action_id}')],
    ])


async def send_pending_actions(message: Message, pending: list[tuple[int, str]]) -> None:
    for action_id, label in pending[:5]:
        await message.answer('Селин предлагает:\n' + label, reply_markup=confirmation_keyboard(action_id))


@router.callback_query(F.data.startswith('agent:'))
async def confirm_agent_action(callback: CallbackQuery) -> None:
    try:
        parts=(callback.data or '').split(':')
        if len(parts) != 3 or parts[1] not in {'yes','no'}:
            await callback.answer('Некорректная кнопка', show_alert=True)
            return
        action_id=int(parts[2])
        async with SessionLocal() as session:
            profile=await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
            profile_id=profile.id
            await session.commit()
        result=await execute_pending(profile_id, action_id, parts[1]=='yes')
        await callback.answer()
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.message.answer(result)
    except ValueError as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
    except Exception:
        log.exception('Confirmed action failed')
        await callback.answer('Изменение не применилось. Проверь данные и попробуй ещё раз.', show_alert=True)
