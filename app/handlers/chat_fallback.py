"""Natural chat is the default, including when no menu/dialog FSM is active."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.handlers.ai_chat import FreeSelinChatState, NAVIGATION_TEXTS, free_chat_message
from app.services.living_rhythm import now_local

router = Router()

@router.message(StateFilter(None), F.text)
async def unhandled_text_is_chat(message: Message, state: FSMContext) -> None:
    text = (message.text or '').strip()
    if not text or text.startswith('/') or text in NAVIGATION_TEXTS:
        return
    await state.set_state(FreeSelinChatState.talking)
    await state.update_data(last_chat_activity_at=now_local().isoformat())
    await free_chat_message(message, state)
