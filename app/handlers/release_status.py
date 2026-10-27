"""Deployment health-checks for the Selin & Tori Telegram bot.

These commands deliberately do not call OpenAI or mutate the economy.
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import text

from app.db import SessionLocal

router = Router()
logger = logging.getLogger(__name__)
RELEASE = '2026-10-27-food-edit-v1'


@router.message(Command('version', 'botversion'))
async def version(message: Message) -> None:
    await message.answer(
        f'✅ Селин и Тори — {RELEASE}\n'
        'Лавка странствий: обработчик подключён.\n'
        'Разговор без кнопки завершения; /selin_status показывает реальные изменения.\n'
        'Еда: /food_edit — исправить или удалить запись.\n'
        'Пауза тренировок: /activity_status.\n'
        'Магазин: /shop; диагностика: /shopcheck.'
    )


@router.message(Command('shopcheck'))
async def shopcheck(message: Message) -> None:
    try:
        async with SessionLocal() as session:
            offers = (await session.execute(
                text('SELECT COUNT(*) FROM selin_wander_shop_offers')
            )).scalar_one()
        await message.answer(
            f'✅ Сборка {RELEASE}\n'
            f'✅ Таблица лавки доступна, товаров в базе: {offers}.\n'
            'Если товары отсутствуют, открой /shop для первой загрузки.'
        )
    except Exception as exc:
        logger.exception('Shop DB healthcheck failed')
        await message.answer(
            f'✅ Сборка {RELEASE}\n'
            f'❌ База лавки недоступна ({type(exc).__name__}).\n'
            'Посмотри ошибки в логах запуска бота на Bothost. '
            'Этот тест ничего не меняет в базе.'
        )
