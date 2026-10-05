from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.db import SessionLocal
from app.keyboards import ai_chat_stop_menu, ai_quest_offer_menu
from app.models import AIQuestOffer
from app.services.ai_engine import ai_enabled, generate_selin_reply
from app.services.ai_features import (
    CHAT_SESSION_XP,
    accept_ai_quest,
    append_recent_message,
    available_chat_sessions,
    begin_chat_session,
    build_ai_context,
    can_offer_ai_quest,
    create_ai_quest_offer,
    get_or_create_ai_state,
    recent_chat_text,
    reserve_ai_call,
    xp_until_next_chat,
    now_local,
)
from app.services.assets import send_reaction
from app.services.levels import level_from_xp
from app.services.rewards import get_or_create_profile

router = Router()


class FreeSelinChatState(StatesGroup):
    talking = State()


@router.callback_query(F.data == "selinchat:free")
async def start_free_chat(callback: CallbackQuery, state: FSMContext) -> None:
    if not ai_enabled():
        await callback.answer()
        await callback.message.answer(
            "Свободный разговор пока выключен: на сервере не задан OPENAI_API_KEY. Остальные функции бота работают как раньше."
        )
        return

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        level, _, _ = level_from_xp(profile.xp)
        if level < 2:
            await callback.answer("Свободный разговор откроется на уровне синхронизации 2.", show_alert=True)
            return

        ai_state = await get_or_create_ai_state(session, profile)
        if ai_state.active_turns_left <= 0:
            if not begin_chat_session(profile, ai_state):
                need = xp_until_next_chat(profile, ai_state)
                await session.commit()
                await callback.answer()
                await send_reaction(
                    callback.message,
                    "selin",
                    "smirk",
                    f"— Поболтать можно. Но связь сейчас слабовата. Заработай ещё {need} XP.\n\n"
                    f"Каждые {CHAT_SESSION_XP} новых XP открывают один свободный разговор. Сам XP и уровень при этом не тратятся.",
                )
                return
        turns = ai_state.active_turns_left
        banked = available_chat_sessions(profile, ai_state)
        await session.commit()

    await state.set_state(FreeSelinChatState.talking)
    await send_reaction(
        callback.message,
        "selin",
        "curious",
        f"— Ладно, я слушаю.\n\nНа эту беседу: {turns} ответов. Ещё разговоров в запасе: {banked}.",
        reply_markup=ai_chat_stop_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "aichat:stop")
async def stop_free_chat(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await send_reaction(callback.message, "selin", "neutral", "— Хорошо. Вернёмся к этому потом.")


@router.message(FreeSelinChatState.talking, F.text)
async def free_chat_message(message: Message, state: FSMContext) -> None:
    user_text = (message.text or "").strip()
    if not user_text:
        return

    # Do not accidentally eat the normal reply-keyboard navigation.
    if user_text in {
        "📊 Сегодня", "🚶 Шаги", "🍽 Еда", "🏋️ Тренировка", "📜 Квесты",
        "🎁 Гардероб", "📖 История", "💬 Селин", "🏆 Ачивки", "📷 Анализ фото",
    }:
        await message.answer("Сначала закончи свободный разговор кнопкой «🛑 Закончить разговор».")
        return

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        ai_state = await get_or_create_ai_state(session, profile)
        if ai_state.active_turns_left <= 0:
            need = xp_until_next_chat(profile, ai_state)
            await session.commit()
            await state.clear()
            await send_reaction(
                message,
                "selin",
                "neutral",
                f"— Всё, на сегодня связь для этого разговора закончилась.\n\nДо следующего разговора: {need} XP.",
            )
            return

        if not await reserve_ai_call(session, profile.id, "chat"):
            await session.commit()
            await message.answer("На сегодня достигнут защитный лимит AI-запросов. Завтра счётчик обнулится.")
            return

        context = await build_ai_context(session, profile)
        recent = recent_chat_text(ai_state)
        context_today = now_local().date()
        can_offer = await can_offer_ai_quest(session, profile.id, context_today)
        await session.commit()

    try:
        result = await generate_selin_reply(
            user_text=user_text,
            context=context,
            recent_chat=recent,
            can_offer_quest=can_offer,
        )
    except Exception as exc:
        print("AI chat error:", repr(exc))
        await message.answer("Связь с Селин сейчас сбоила. XP за разговор не пропал — попробуй ещё раз чуть позже.")
        return

    offer = None
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        ai_state = await get_or_create_ai_state(session, profile)
        if ai_state.active_turns_left <= 0:
            await state.clear()
            await message.answer("Этот разговор уже закончился.")
            return

        append_recent_message(ai_state, "user", user_text)
        append_recent_message(ai_state, "assistant", str(result.get("text", "")))
        ai_state.active_turns_left -= 1

        if result.get("offer_quest") and await can_offer_ai_quest(session, profile.id, context_today):
            title = (result.get("quest_title") or "").strip()
            if title:
                offer = await create_ai_quest_offer(
                    session,
                    profile,
                    title=title,
                    difficulty=str(result.get("quest_difficulty") or "easy"),
                    reason=str(result.get("quest_reason") or ""),
                )
        turns_left = ai_state.active_turns_left
        await session.commit()

    text = str(result.get("text") or "Хм.")
    if offer:
        text += (
            f"\n\n📜 Селин предлагает задание: {offer.title}"
            f"\nНаграда: +{offer.reward_xp} XP · +{offer.reward_coins} монет"
        )
        if offer.reason:
            text += f"\n{offer.reason}"
        markup = ai_quest_offer_menu(offer.id)
    else:
        text += f"\n\nОсталось ответов в этой беседе: {turns_left}."
        markup = ai_chat_stop_menu() if turns_left > 0 else None

    await send_reaction(
        message,
        "selin",
        str(result.get("emotion") or "neutral"),
        text,
        reply_markup=markup,
    )
    if result.get("show_tori") and str(result.get("tori_text") or "").strip():
        await send_reaction(
            message,
            "tori",
            str(result.get("tori_emotion") or "neutral"),
            str(result.get("tori_text")),
        )
    if turns_left <= 0:
        await state.clear()


@router.callback_query(F.data.startswith("aiquest:accept:"))
async def accept_quest(callback: CallbackQuery, state: FSMContext) -> None:
    offer_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        offer = await session.get(AIQuestOffer, offer_id)
        if not offer or offer.user_id != profile.id or offer.status != "offered":
            await callback.answer("Это предложение уже закрыто.", show_alert=True)
            return
        quest = await accept_ai_quest(session, profile, offer)
        await session.commit()
    await callback.answer()
    current_state = await state.get_state()
    markup = ai_chat_stop_menu() if current_state == FreeSelinChatState.talking.state else None
    await send_reaction(
        callback.message,
        "selin",
        "smirk",
        f"— Хорошо. Тогда сделай: {quest.title}\n\nКогда закончишь, отметь его в 📜 Квесты.",
        reply_markup=markup,
    )


@router.callback_query(F.data.startswith("aiquest:decline:"))
async def decline_quest(callback: CallbackQuery, state: FSMContext) -> None:
    offer_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        offer = await session.get(AIQuestOffer, offer_id)
        if not offer or offer.user_id != profile.id or offer.status != "offered":
            await callback.answer("Это предложение уже закрыто.", show_alert=True)
            return
        offer.status = "declined"
        await session.commit()
    await callback.answer()
    current_state = await state.get_state()
    markup = ai_chat_stop_menu() if current_state == FreeSelinChatState.talking.state else None
    await send_reaction(
        callback.message, "selin", "neutral", "— Не хочешь — не надо. Я просто предложила.",
        reply_markup=markup,
    )
