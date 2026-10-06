from __future__ import annotations

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.db import SessionLocal
from app.keyboards import ai_chat_stop_menu, ai_initiative_stop_menu, ai_quest_offer_menu
from app.models import AIQuestOffer, Notification
from app.services.ai_engine import ai_enabled, generate_selin_reply
from app.services.ai_features import (
    accept_ai_quest,
    append_recent_message,
    build_ai_context,
    can_offer_ai_quest,
    create_ai_quest_offer,
    get_or_create_ai_state,
    recent_chat_text,
    reserve_ai_call,
    store_ai_memories,
    now_local,
)
from app.services.assets import send_reaction
from app.services.dialogue import get_tori_autonomous_response
from app.services.rewards import get_or_create_profile
from app.services.tori import reward_tori_interaction
from app.services.story import advance_week1_if_due, get_or_create_story_progress, initialize_week1_v2, selin_chat_gate

router = Router()


class FreeSelinChatState(StatesGroup):
    talking = State()


class InitiativeSelinChatState(StatesGroup):
    talking = State()


NAVIGATION_TEXTS = {
    "📊 Сегодня", "🚶 Шаги", "🍽 Еда", "🏋️ Тренировка", "📜 Квесты",
    "🎁 Гардероб", "📖 История", "💬 Селин", "🦊 Тори", "🏆 Ачивки", "📷 Анализ фото", "🎯 Челлендж",
}


async def _initiative_notification(
    session,
    profile_id: int,
    notification_id: int,
) -> Notification | None:
    row = await session.get(Notification, notification_id)
    if not row or row.user_id != profile_id or row.kind not in {"autonomous_event", "activity_nudge"}:
        return None
    return row


async def _active_initiative_notification(session, profile_id: int) -> Notification | None:
    rows = list(
        (
            await session.scalars(
                select(Notification)
                .where(
                    Notification.user_id == profile_id,
                    Notification.kind.in_(["autonomous_event", "activity_nudge"]),
                )
                .order_by(Notification.id.desc())
                .limit(10)
            )
        ).all()
    )
    for row in rows:
        payload = dict(row.payload or {})
        if (
            payload.get("event_type") == "selin"
            and payload.get("status") in {"open", "talking"}
            and row.scheduled_at.date() == now_local().date()
        ):
            return row
    return None


async def _supersede_initiative_chats(
    session,
    profile_id: int,
    *,
    except_notification_id: int | None = None,
) -> None:
    query = (
        select(Notification)
        .where(
            Notification.user_id == profile_id,
            Notification.kind.in_(["autonomous_event", "activity_nudge"]),
        )
        .order_by(Notification.id.desc())
        .limit(10)
    )
    if except_notification_id is not None:
        query = query.where(Notification.id != except_notification_id)
    rows = list((await session.scalars(query)).all())
    for row in rows:
        payload = dict(row.payload or {})
        if payload.get("event_type") == "selin" and payload.get("status") in {"open", "talking"}:
            payload["status"] = "superseded"
            row.payload = payload


async def _current_chat_markup(state: FSMContext):
    current_state = await state.get_state()
    if current_state == FreeSelinChatState.talking.state:
        return ai_chat_stop_menu()
    if current_state == InitiativeSelinChatState.talking.state:
        notification_id = (await state.get_data()).get("initiative_notification_id")
        if notification_id:
            return ai_initiative_stop_menu(int(notification_id))
    return None


@router.callback_query(F.data == "selinchat:free")
async def start_free_chat(callback: CallbackQuery, state: FSMContext) -> None:
    if not ai_enabled():
        await callback.answer()
        await callback.message.answer(
            "Свободный разговор пока выключен: на сервере не задан OPENAI_API_KEY. Остальные функции бота работают как раньше."
        )
        return

    today = now_local().date()
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        progress = await get_or_create_story_progress(session, profile.id, today)
        initialize_week1_v2(progress, profile, today)
        advance_week1_if_due(progress, today)
        allowed, reason = selin_chat_gate(profile, progress, today)
        if not allowed:
            await session.commit()
            await callback.answer()
            await send_reaction(callback.message, "selin", "neutral", f"— Сейчас не получится нормально поговорить.\n\n{reason}")
            return
        await get_or_create_ai_state(session, profile)
        await _supersede_initiative_chats(session, profile.id)
        await session.commit()

    await state.set_state(FreeSelinChatState.talking)
    await send_reaction(
        callback.message,
        "selin",
        "curious",
        "— Ладно, я слушаю. Можем говорить сколько хочешь, пока связь по сюжету не занята чем-то более весёлым.",
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
    if user_text in NAVIGATION_TEXTS:
        await message.answer("Сначала закончи свободный разговор кнопкой «🛑 Закончить разговор».")
        return

    today = now_local().date()
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        progress = await get_or_create_story_progress(session, profile.id, today)
        initialize_week1_v2(progress, profile, today)
        advance_week1_if_due(progress, today)
        allowed, reason = selin_chat_gate(profile, progress, today)
        if not allowed:
            await session.commit()
            await state.clear()
            await send_reaction(message, "selin", "neutral", f"— Связь опять срывается.\n\n{reason}")
            return

        if not await reserve_ai_call(session, profile.id, "chat"):
            await session.commit()
            await message.answer("AI сейчас временно недоступен.")
            return
        ai_state = await get_or_create_ai_state(session, profile)
        context = await build_ai_context(session, profile)
        recent = recent_chat_text(ai_state)
        can_offer = await can_offer_ai_quest(session, profile.id, today)
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
        await message.answer("Связь с Селин сейчас сбоила. Попробуй ещё раз чуть позже.")
        return

    offer = None
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        ai_state = await get_or_create_ai_state(session, profile)
        append_recent_message(ai_state, "user", user_text)
        append_recent_message(ai_state, "assistant", str(result.get("text", "")))
        await store_ai_memories(session, profile.id, result.get("memories_to_save"), source="selin_chat")

        if result.get("offer_quest") and await can_offer_ai_quest(session, profile.id, today):
            title = (result.get("quest_title") or "").strip()
            if title:
                offer = await create_ai_quest_offer(
                    session,
                    profile,
                    title=title,
                    difficulty=str(result.get("quest_difficulty") or "easy"),
                    reason=str(result.get("quest_reason") or ""),
                )
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
        markup = ai_chat_stop_menu()

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


@router.callback_query(F.data.startswith("initiative:reply:"))
async def start_initiative_chat(callback: CallbackQuery, state: FSMContext) -> None:
    notification_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        notification = await _initiative_notification(session, profile.id, notification_id)
        if not notification:
            await callback.answer("Это сообщение больше недоступно.", show_alert=True)
            return
        payload = dict(notification.payload or {})
        if payload.get("event_type") != "selin":
            await callback.answer("На это сообщение нельзя ответить.", show_alert=True)
            return
        status = str(payload.get("status") or "")
        if status not in {"open", "talking"}:
            await callback.answer("Этот разговор уже закрыт.", show_alert=True)
            return
        if notification.scheduled_at.date() != now_local().date():
            payload["status"] = "expired"
            notification.payload = payload
            await session.commit()
            await callback.answer("Это сообщение уже осталось в прошлом.", show_alert=True)
            return
        payload["status"] = "talking"
        notification.payload = payload
        await _supersede_initiative_chats(
            session,
            profile.id,
            except_notification_id=notification_id,
        )
        await session.commit()

    await state.set_state(InitiativeSelinChatState.talking)
    await state.update_data(initiative_notification_id=notification_id)
    await callback.answer()
    # Не плодим промежуточное "я слушаю". Само сообщение Селин уже является
    # началом разговора; после нажатия просто меняем кнопки на завершение.
    try:
        await callback.message.edit_reply_markup(reply_markup=ai_initiative_stop_menu(notification_id))
    except Exception:
        pass


@router.callback_query(F.data.startswith("initiative:later:"))
async def dismiss_initiative(callback: CallbackQuery, state: FSMContext) -> None:
    notification_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        notification = await _initiative_notification(session, profile.id, notification_id)
        if not notification:
            await callback.answer("Это сообщение больше недоступно.", show_alert=True)
            return
        payload = dict(notification.payload or {})
        if payload.get("status") not in {"open", "talking"}:
            await callback.answer("Это сообщение уже закрыто.", show_alert=True)
            return
        payload["status"] = "dismissed"
        notification.payload = payload
        await session.commit()

    data = await state.get_data()
    if data.get("initiative_notification_id") == notification_id:
        await state.clear()
    await callback.answer()
    await send_reaction(callback.message, "selin", "neutral", "— Хорошо. Потом так потом.")


@router.callback_query(F.data.startswith("initiative:stop:"))
async def stop_initiative_chat(callback: CallbackQuery, state: FSMContext) -> None:
    notification_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        notification = await _initiative_notification(session, profile.id, notification_id)
        if not notification:
            await callback.answer("Этот разговор больше недоступен.", show_alert=True)
            return
        payload = dict(notification.payload or {})
        if payload.get("status") != "talking":
            await callback.answer("Этот разговор уже закрыт.", show_alert=True)
            return
        payload["status"] = "stopped"
        notification.payload = payload
        await session.commit()

    await state.clear()
    await callback.answer()
    await send_reaction(callback.message, "selin", "neutral", "— Ладно. Продолжим в другой раз.")


async def _handle_initiative_message(
    message: Message,
    state: FSMContext,
    notification_id: int,
) -> None:
    user_text = (message.text or "").strip()
    if not user_text:
        return
    if user_text in NAVIGATION_TEXTS:
        await message.answer("Сначала закончи разговор кнопкой «🛑 Закончить разговор».")
        return

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        notification = await _initiative_notification(session, profile.id, notification_id)
        if not notification:
            await state.clear()
            await message.answer("Этот разговор больше недоступен.")
            return
        payload = dict(notification.payload or {})
        if payload.get("event_type") != "selin" or payload.get("status") != "talking":
            await state.clear()
            await message.answer("Этот разговор уже закончился.")
            return
        if not await reserve_ai_call(session, profile.id, "chat"):
            await session.commit()
            await message.answer("На сегодня достигнут защитный лимит AI-запросов. Разговор можно продолжить завтра.")
            return
        context = await build_ai_context(session, profile)
        ai_state = await get_or_create_ai_state(session, profile)
        recent = recent_chat_text(ai_state)
        await session.commit()

    try:
        result = await generate_selin_reply(
            user_text=user_text,
            context=context,
            recent_chat=recent,
            can_offer_quest=False,
        )
    except Exception as exc:
        print("AI initiative reply error:", repr(exc))
        await message.answer("Связь сейчас сбоила. Попробуй ответить ещё раз чуть позже.")
        return

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        notification = await _initiative_notification(session, profile.id, notification_id)
        if not notification:
            await state.clear()
            await message.answer("Этот разговор больше недоступен.")
            return
        payload = dict(notification.payload or {})
        if payload.get("status") != "talking":
            await state.clear()
            await message.answer("Этот разговор уже закрыт.")
            return

        ai_state = await get_or_create_ai_state(session, profile)
        append_recent_message(ai_state, "user", user_text)
        append_recent_message(ai_state, "assistant", str(result.get("text") or ""))
        await store_ai_memories(session, profile.id, result.get("memories_to_save"), source="initiative_chat")
        notification.payload = payload
        await session.commit()

    text = str(result.get("text") or "Хм.")
    markup = ai_initiative_stop_menu(notification_id)

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


@router.message(InitiativeSelinChatState.talking, F.text)
async def initiative_chat_message(message: Message, state: FSMContext) -> None:
    notification_id = (await state.get_data()).get("initiative_notification_id")
    if not notification_id:
        await state.clear()
        await message.answer("Я потеряла начало разговора. Нажми «Ответить» под сообщением Селин ещё раз.")
        return
    await _handle_initiative_message(message, state, int(notification_id))


@router.callback_query(F.data.startswith("toriauto:react:"))
async def react_to_tori_event(callback: CallbackQuery) -> None:
    _, _, notification_raw, choice_id = callback.data.split(":", 3)
    notification_id = int(notification_raw)

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        notification = await _initiative_notification(session, profile.id, notification_id)
        if not notification:
            await callback.answer("Эта сценка больше недоступна.", show_alert=True)
            return
        payload = dict(notification.payload or {})
        if payload.get("event_type") != "tori" or payload.get("status") != "open":
            await callback.answer("Тори уже убежал по своим делам.", show_alert=True)
            return
        response = get_tori_autonomous_response(str(payload.get("event_id") or ""), choice_id)
        if not response:
            await callback.answer("Такого варианта больше нет.", show_alert=True)
            return
        payload["status"] = "resolved"
        payload["choice"] = choice_id
        notification.payload = payload
        bond_rewarded = await reward_tori_interaction(
            session, profile, source=f"event:{notification.id}"
        )
        await session.commit()

    emotion, text = response
    if bond_rewarded:
        text += "\n\n+1 связь с Тори"
    await callback.answer()
    await send_reaction(callback.message, "tori", emotion, text)


@router.message(F.text)
async def recover_initiative_chat(message: Message, state: FSMContext) -> None:
    if await state.get_state() is not None:
        raise SkipHandler
    user_text = (message.text or "").strip()
    if not user_text or user_text.startswith("/") or user_text in NAVIGATION_TEXTS:
        raise SkipHandler
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        notification = await _active_initiative_notification(session, profile.id)
        if notification:
            payload = dict(notification.payload or {})
            if payload.get("status") == "open":
                payload["status"] = "talking"
                notification.payload = payload
                await _supersede_initiative_chats(
                    session,
                    profile.id,
                    except_notification_id=notification.id,
                )
                await session.commit()
    if not notification:
        raise SkipHandler
    await state.set_state(InitiativeSelinChatState.talking)
    await state.update_data(initiative_notification_id=notification.id)
    await _handle_initiative_message(message, state, notification.id)


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
    markup = await _current_chat_markup(state)
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
    markup = await _current_chat_markup(state)
    await send_reaction(
        callback.message, "selin", "neutral", "— Не хочешь — не надо. Я просто предложила.",
        reply_markup=markup,
    )
