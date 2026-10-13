import asyncio

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.types import Message, CallbackQuery

from app.config import settings
from app.db import SessionLocal, init_db
from app.handlers import ai_chat, challenges, coaching, common, gacha, photo_analysis, quick_input, self_workouts, selin_chat, story, tori_chat, workouts
from app.services.scheduler import notification_worker
from app.services.seed import seed_scrolls


class ReleaseConversationOnNavigation(BaseMiddleware):
    """Do not leave an old Selin/Tori chat trapping unrelated menu actions."""

    async def __call__(self, handler, event, data):
        state = data.get("state")
        if state:
            from app.handlers.ai_chat import (
                FreeSelinChatState, InitiativeSelinChatState, NAVIGATION_TEXTS,
            )
            from app.handlers.tori_chat import FreeToriChatState
            from app.handlers.quick_input import QuickInputState
            active = await state.get_state()
            talking = {
                FreeSelinChatState.talking.state,
                InitiativeSelinChatState.talking.state,
                FreeToriChatState.talking.state,
                QuickInputState.choose.state,
            }
            if active in talking:
                is_navigation = isinstance(event, Message) and event.text in NAVIGATION_TEXTS
                is_menu_callback = isinstance(event, CallbackQuery) and str(event.data or "").startswith((
                    "food:", "drink:", "wardrobe:", "wc:", "shop:", "quest:",
                    "manualworkout:", "photoai:", "challenges:", "challenge:",
                ))
                if is_navigation or is_menu_callback:
                    await state.clear()
        return await handler(event, data)


async def main() -> None:
    await init_db()

    async with SessionLocal() as session:
        await seed_scrolls(session)
        await session.commit()

    bot = Bot(settings.bot_token)
    dp = Dispatcher()
    dp.message.middleware(ReleaseConversationOnNavigation())
    dp.callback_query.middleware(ReleaseConversationOnNavigation())
    dp.include_router(common.router)
    dp.include_router(coaching.router)
    dp.include_router(challenges.router)
    dp.include_router(story.router)
    dp.include_router(selin_chat.router)
    dp.include_router(tori_chat.router)
    dp.include_router(ai_chat.router)
    dp.include_router(workouts.router)
    dp.include_router(self_workouts.router)
    dp.include_router(gacha.router)
    dp.include_router(photo_analysis.router)
    dp.include_router(quick_input.router)

    worker = asyncio.create_task(notification_worker(bot))
    try:
        await dp.start_polling(bot)
    finally:
        worker.cancel()


if __name__ == "__main__":
    asyncio.run(main())
