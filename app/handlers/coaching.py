from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app.db import SessionLocal
from app.keyboards import back_menu
from app.services.ai_features import (
    clear_user_memories,
    get_calorie_target,
    get_coach_rules,
    set_calorie_target,
    set_coach_rules,
    user_memory_text,
)
from app.services.rewards import get_or_create_profile
from app.services.evening_review import create_evening_review
from app.services.assets import send_reaction
from app.services.ai_features import now_local

router = Router()


class CoachRulesState(StatesGroup):
    waiting_rules = State()


@router.message(Command("trainer_rules"))
async def trainer_rules_start(message: Message, state: FSMContext) -> None:
    await state.set_state(CoachRulesState.waiting_rules)
    await message.answer(
        "Пришли рекомендации тренера одним сообщением. Я буду передавать их ИИ для вечернего разбора и выбора челленджей.\n\n"
        "Можно написать хоть списком, хоть обычным текстом. Новое сообщение полностью заменит только твои дополнительные правила; базовые рекомендации останутся.",
        reply_markup=back_menu("state:cancel", "❌ Отмена"),
    )


@router.message(CoachRulesState.waiting_rules)
async def trainer_rules_save(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Нужен текст рекомендаций.")
        return
    if len(text) > 8000:
        await message.answer("Слишком длинно. Уложись примерно в 8000 символов.")
        return
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        await set_coach_rules(session, profile.id, text)
        await session.commit()
    await state.clear()
    await message.answer("Сохранила дополнительные рекомендации тренера. Базовые правила тоже останутся активны; вечерний анализ и челленджи будут учитывать всё вместе.")


@router.message(Command("trainer_rules_show"))
async def trainer_rules_show(message: Message) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        rules = await get_coach_rules(session, profile.id)
    await message.answer(rules if rules else "Рекомендации тренера пока не заданы. Используй /trainer_rules.")


@router.message(Command("trainer_rules_clear"))
async def trainer_rules_clear(message: Message) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        await set_coach_rules(session, profile.id, "")
        await session.commit()
    await message.answer("Дополнительные рекомендации очищены. Базовые правила питания из текущего плана остаются активны.")


@router.message(Command("calorie_target"))
@router.message(Command("nutrition_goal"))
async def calorie_target_command(message: Message) -> None:
    raw = (message.text or "").split(maxsplit=1)
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        if len(raw) == 1:
            target = await get_calorie_target(session, profile.id)
            await message.answer(
                f"Текущий ориентир: около {target} ккал в день.\n\n"
                f"Изменить: /calorie_target 1600"
            )
            return
        try:
            requested = int(raw[1].strip())
        except ValueError:
            await message.answer("Напиши число, например: /calorie_target 1500")
            return
        if requested < 1200:
            await message.answer(
                "Ниже 1200 ккал как автоматический игровой ориентир я не ставлю. "
                "Если тренер или врач назначил другой план, лучше сначала уточнить его отдельно."
            )
            return
        if requested > 4000:
            await message.answer("Это уже слишком большое значение для этого трекера. Укажи число до 4000.")
            return
        target = await set_calorie_target(session, profile.id, requested)
        await session.commit()
    await message.answer(
        f"Ориентир обновлён: около {target} ккал в день. "
        "Селин будет сравнивать с ним вечерний разбор, но не считать недобор отдельной победой."
    )


@router.message(Command("selin_memory"))
async def selin_memory_show(message: Message) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        memories = await user_memory_text(session, profile.id, limit=30)
    await message.answer("Что Селин помнит о тебе:\n\n" + memories)


@router.message(Command("selin_forget_all"))
async def selin_memory_clear(message: Message) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        await clear_user_memories(session, profile.id)
        await session.commit()
    await message.answer("Память Селин о личных деталях очищена.")

@router.message(Command("evening_review"))
async def evening_review_now(message: Message) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        review, chest, _ = await create_evening_review(
            session,
            profile,
            now_local().date(),
            force=True,
            finalize_challenge=False,
        )
        await session.commit()
    text = review.text
    if review.tomorrow_focus:
        text += f"\n\nФокус на завтра: {review.tomorrow_focus}"
    if chest:
        text += "\n✨ +1 Сундук испытания."
    await send_reaction(message, "selin", review.emotion or "neutral", text)

