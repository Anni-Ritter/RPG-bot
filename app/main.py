import asyncio
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("selin.startup")

from aiogram import Bot, Dispatcher

from app.config import settings
from app.db import SessionLocal, init_db
from app.handlers import ai_chat, challenges, coaching, common, gacha, photo_analysis, recipe_book, nutrition_history, self_workouts, selin_chat, story, tori_chat, workouts
from app.services.scheduler import notification_worker
from app.services.seed import seed_scrolls


# SELIN_LIVING_RHYTHM_20261027_BEGIN
from app.services import living_rhythm as _living_rhythm_model_registry
from app.handlers import living_rhythm as living_rhythm_handlers
from app.services.living_rhythm_worker import rhythm_worker
# SELIN_LIVING_RHYTHM_20261027_END

# SELIN_WANDER_SHOP_20261027_BEGIN
from app.services import wandering_shop as _wandering_shop_model_registry
from app.handlers import wandering_shop as wandering_shop_handlers
from app.handlers import release_status as release_status_handlers
# SELIN_WANDER_SHOP_20261027_END

# SELIN_ACTIVITY_BREAK_20261027_BEGIN
# Register the ORM model before init_db() creates SQL tables.
from app.handlers import activity_breaks as activity_break_handlers
from app.handlers.activity_breaks import ActivityBreakMiddleware
from app.services import activity_breaks as _activity_break_model_registry
# SELIN_ACTIVITY_BREAK_20261027_END

async def main() -> None:
    await init_db()

    async with SessionLocal() as session:
        await seed_scrolls(session)
        await session.commit()

    bot = Bot(settings.bot_token)
    log.info("SELIN_RELEASE=2026-10-27-shop-deploy-fix; main=%s", __file__)
    dp = Dispatcher()
    # Run before the active chat FSM routes: even an ordinary message can set a pause.
    dp.message.outer_middleware(ActivityBreakMiddleware())
    dp.include_router(activity_break_handlers.router)
    # Explicit commands before chat/FSM handlers, regardless of current conversation.
    dp.include_router(release_status_handlers.router)
    dp.include_router(wandering_shop_handlers.router)
    dp.include_router(common.router)
    dp.include_router(coaching.router)
    dp.include_router(nutrition_history.router)
    dp.include_router(challenges.router)
    dp.include_router(story.router)
    dp.include_router(selin_chat.router)
    dp.include_router(tori_chat.router)
    dp.include_router(recipe_book.router)
    dp.include_router(living_rhythm_handlers.router)
    dp.include_router(ai_chat.router)
    dp.include_router(workouts.router)
    dp.include_router(self_workouts.router)
    dp.include_router(gacha.router)
    dp.include_router(photo_analysis.router)

    worker = asyncio.create_task(notification_worker(bot))
    rhythm_task = asyncio.create_task(rhythm_worker(bot))
    try:
        await dp.start_polling(bot)
    finally:
        rhythm_task.cancel()
        worker.cancel()


if __name__ == "__main__":
    asyncio.run(main())
