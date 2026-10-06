from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, User

from app.db import SessionLocal
from app.keyboards import tori_chat_menu, tori_chat_stop_menu
from app.services.achievements import achievement_messages, check_achievements
from app.services.ai_engine import ai_enabled, generate_tori_reply
from app.services.ai_features import build_ai_context, reserve_ai_call
from app.services.assets import send_reaction
from app.services.rewards import get_or_create_profile
from app.services.story import tori_bond_label
from app.services.tori import (
    TORI_BOND_HELP,
    TORI_INTERACTION_REWARD_LIMIT,
    reward_tori_interaction,
    rewarded_tori_interactions_today,
)

router = Router()


class FreeToriChatState(StatesGroup):
    talking = State()


NAVIGATION_TEXTS = {
    "📊 Сегодня", "🚶 Шаги", "🍽 Еда", "🏋️ Тренировка", "📜 Квесты",
    "🎁 Гардероб", "📖 История", "💬 Селин", "🦊 Тори", "🏆 Ачивки",
    "📷 Анализ фото", "🎯 Челлендж",
}

ACTION_TEXT = {
    "pet": "Проводник гладит Тори и чешет его за ушами.",
    "play": "Проводник предлагает Тори немного поиграть.",
    "call": "Проводник зовёт Тори к себе и пытается привлечь его внимание.",
}

FALLBACK = {
    "pet": ("affectionate", "Тори сначала делает вид, что это совершенно обычное дело, а потом сам подставляет голову под ладонь."),
    "play": ("happy", "Тори мгновенно оживляется и принимает предложение так, будто весь день только этого и ждал."),
    "call": ("curious", "Тори настораживает уши, смотрит в твою сторону и через секунду уже оказывается рядом."),
    "free": ("curious", "Тори внимательно следит за тобой и явно пытается понять, что ты задумала."),
}


async def _tori_response(message: Message, action: str, source: str, user: User | None = None) -> None:
    user = user or message.from_user
    if user is None:
        return
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, user.id, user.full_name)
        context = await build_ai_context(session, profile)
        if int(context.get("story_day") or 1) < 2:
            await session.commit()
            await message.answer("Тори пока ещё не появился в истории. Сначала познакомься с ним через 📖 История.")
            return
        can_call = ai_enabled() and await reserve_ai_call(session, profile.id, "tori_chat")
        await session.commit()

    result = None
    if can_call:
        try:
            result = await generate_tori_reply(user_action=action, context=context)
        except Exception as exc:
            print("Tori AI interaction error:", repr(exc))

    if result:
        emotion = str(result.get("emotion") or "curious")
        text = str(result.get("text") or "Тори внимательно смотрит на тебя.")
    else:
        emotion, text = FALLBACK.get(source, FALLBACK["free"])

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, user.id, user.full_name)
        rewarded = await reward_tori_interaction(session, profile, source=source)
        unlocked = await check_achievements(session, profile)
        bond = profile.tori_bond
        rewarded_today = await rewarded_tori_interactions_today(session, profile.id)
        await session.commit()

    if rewarded:
        text += f"\n\n+1 связь с Тори · теперь {bond}"
    elif rewarded_today >= TORI_INTERACTION_REWARD_LIMIT:
        text += "\n\nСвязь за общение на сегодня уже набрана, но взаимодействовать с ним можно сколько угодно."

    await send_reaction(message, "tori", emotion, text, reply_markup=tori_chat_stop_menu() if source == "free" else tori_chat_menu())
    for item in achievement_messages(unlocked):
        await message.answer(item)


@router.message(F.text == "🦊 Тори")
async def open_tori(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        context = await build_ai_context(session, profile)
        if int(context.get("story_day") or 1) < 2:
            await session.commit()
            await message.answer("Тори пока ещё не появился. Загляни в 📖 История — вы познакомитесь там.")
            return
        rewarded_today = await rewarded_tori_interactions_today(session, profile.id)
        bond = profile.tori_bond
        label = tori_bond_label(bond)
        await session.commit()
    left = max(0, TORI_INTERACTION_REWARD_LIMIT - rewarded_today)
    await send_reaction(
        message,
        "tori",
        "curious",
        f"Тори здесь.\n\nСвязь: {bond} · {label}\nСегодня ещё {left} разных взаимодействия могут дать +1 связи.",
        reply_markup=tori_chat_menu(),
    )


@router.callback_query(F.data == "torichat:help")
async def tori_help(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(TORI_BOND_HELP, reply_markup=tori_chat_menu())


@router.callback_query(F.data.startswith("torichat:action:"))
async def tori_action(callback: CallbackQuery) -> None:
    action_id = callback.data.rsplit(":", 1)[1]
    action = ACTION_TEXT.get(action_id)
    if not action:
        await callback.answer("Тори не понял, что происходит.", show_alert=True)
        return
    await callback.answer()
    await _tori_response(callback.message, action, action_id, callback.from_user)


@router.callback_query(F.data == "torichat:free")
async def start_tori_free(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(FreeToriChatState.talking)
    await callback.answer()
    await send_reaction(
        callback.message,
        "tori",
        "curious",
        "Можешь написать, что делаешь или говоришь Тори. Он словами не ответит, но отреагирует.",
        reply_markup=tori_chat_stop_menu(),
    )


@router.callback_query(F.data == "torichat:stop")
async def stop_tori_free(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await send_reaction(callback.message, "tori", "happy", "Тори ещё немного крутится рядом, а потом убегает по своим крайне важным лисьим делам.", reply_markup=tori_chat_menu())


@router.message(FreeToriChatState.talking, F.text)
async def tori_free_message(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        return
    if text in NAVIGATION_TEXTS:
        await message.answer("Сначала закончи взаимодействие с Тори кнопкой «🛑 Закончить».")
        return
    await _tori_response(message, text, "free")
