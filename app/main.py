import asyncio

from aiogram import Bot, Dispatcher

from app.config import settings
from app.db import SessionLocal, init_db
from app.handlers import common, gacha, self_workouts, story, workouts
from app.services.scheduler import notification_worker
from app.services.seed import seed_scrolls


async def main() -> None:
    await init_db()

    async with SessionLocal() as session:
        await seed_scrolls(session)
        await session.commit()

    bot = Bot(settings.bot_token)
    dp = Dispatcher()
    dp.include_router(common.router)
    dp.include_router(story.router)
    dp.include_router(workouts.router)
    dp.include_router(self_workouts.router)
    dp.include_router(gacha.router)

    worker = asyncio.create_task(notification_worker(bot))
    try:
        await dp.start_polling(bot)
    finally:
        worker.cancel()


if __name__ == "__main__":
    asyncio.run(main())
