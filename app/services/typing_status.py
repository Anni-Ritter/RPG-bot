"""Keep Telegram typing status alive only while a generation is in flight."""
from __future__ import annotations

from typing import Awaitable, TypeVar

from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

T = TypeVar("T")


async def while_typing(message: Message, job: Awaitable[T]) -> T:
    """A bounded chat action: stops on success, exception, or cancellation.

    Construct `job` only after validation and reservation. The helper never
    sends user-visible placeholder messages and never masks an AI failure.
    """
    async with ChatActionSender.typing(
        bot=message.bot,
        chat_id=message.chat.id,
        interval=4.0,
        initial_sleep=0.0,
    ):
        return await job
