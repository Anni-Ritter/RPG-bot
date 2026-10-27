import asyncio

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
# SELIN_WANDER_SHOP_20261027_END

async def main() -> None:
    await init_db()

    async with SessionLocal() as session:
        await seed_scrolls(session)
        await session.commit()

    bot = Bot(settings.bot_token)
    dp = Dispatcher()
    dp.include_router(common.router)
    dp.include_router(coaching.router)
    dp.include_router(nutrition_history.router)
    dp.include_router(challenges.router)
    dp.include_router(story.router)
    dp.include_router(selin_chat.router)
    dp.include_router(tori_chat.router)
    dp.include_router(recipe_book.router)
    dp.include_router(living_rhythm_handlers.router)
    dp.include_router(wandering_shop_handlers.router)
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
