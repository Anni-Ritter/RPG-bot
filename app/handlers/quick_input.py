"""One-message entry: ask what an arbitrary text is for, without sending the user through menus."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db import SessionLocal
from app.keyboards import food_analysis_menu, food_category_menu
from app.models import AIImageAnalysis
from app.services.ai_engine import ai_enabled, analyze_food_text
from app.services.ai_features import reserve_ai_call
from app.services.assets import replace_text_view
from app.services.nutrition import can_scale_by_grams, normalize_food_result
from app.services.rewards import get_or_create_profile
from app.handlers.photo_analysis import _food_result_text
from app.handlers.ai_chat import FreeSelinChatState, free_chat_message

router = Router()
_active_food_requests: set[int] = set()


class QuickInputState(StatesGroup):
    choose = State()
    working = State()


def intent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍽 Записать еду / напиток", callback_data="quick:food")],
        [InlineKeyboardButton(text="💬 Ответить Селин", callback_data="quick:chat")],
        [InlineKeyboardButton(text="🚶 Отметить шаги", callback_data="quick:steps")],
        [InlineKeyboardButton(text="❌ Не записывать", callback_data="quick:cancel")],
    ])


@router.message(StateFilter(None), F.text)
async def unsolicited_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return
    await state.set_state(QuickInputState.choose)
    await state.update_data(quick_text=text[:2000])
    await message.answer(
        "Что сделать с сообщением? Я не буду ничего записывать, пока ты не выберешь.",
        reply_markup=intent_keyboard(),
    )


@router.callback_query(F.data == "quick:cancel")
async def quick_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await replace_text_view(callback.message, "Хорошо, оставим это сообщение без действий.")
    await callback.answer()


@router.callback_query(F.data == "quick:steps")
async def quick_steps(callback: CallbackQuery, state: FSMContext) -> None:
    from app.handlers.common import StepState
    await state.set_state(StepState.waiting_steps)
    await replace_text_view(callback.message, "Сколько шагов сегодня? Пришли число, например 7200.")
    await callback.answer()


@router.callback_query(F.data == "quick:chat")
async def quick_chat(callback: CallbackQuery, state: FSMContext) -> None:
    from app.services.rewards import get_or_create_profile
    from app.services.story import get_or_create_story_progress, initialize_week1_v2, advance_week1_if_due, selin_chat_gate
    from app.services.ai_features import now_local
    from app.services.assets import send_reaction
    from app.handlers.ai_chat import _supersede_initiative_chats
    data = await state.get_data()
    text = str(data.get("quick_text") or "").strip()
    if not text:
        await state.clear()
        await callback.answer("Сообщение не сохранилось. Напиши ещё раз.", show_alert=True)
        return
    if not ai_enabled():
        await callback.answer("Общение с ИИ сейчас недоступно.", show_alert=True)
        return
    today = now_local().date()
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        progress = await get_or_create_story_progress(session, profile.id, today)
        initialize_week1_v2(progress, profile, today)
        advance_week1_if_due(progress, today)
        allowed, reason = selin_chat_gate(profile, progress, today)
        if allowed:
            await _supersede_initiative_chats(session, profile.id)
        await session.commit()
    if not allowed:
        await state.clear()
        await callback.answer()
        await send_reaction(callback.message, "selin", "neutral", f"Сейчас связь не позволяет разговаривать. {reason}")
        return
    await state.set_state(FreeSelinChatState.talking)
    await state.update_data(last_chat_activity_at=now_local().isoformat())
    await replace_text_view(callback.message, "💬 Передаю сообщение Селин. Можешь дальше писать просто так — разговор сам завершится после паузы.")
    await callback.answer()
    # The callback message is the bot's message; pass the real Telegram user
    # separately to avoid accidentally creating a profile for the bot itself.
    await free_chat_message(callback.message, state, user_text_override=text, actor_override=callback.from_user)


@router.callback_query(F.data == "quick:food")
async def quick_food(callback: CallbackQuery, state: FSMContext) -> None:
    # Prevent double-taps while the network request is in flight.
    user_id = callback.from_user.id
    if user_id in _active_food_requests:
        await callback.answer("Уже считаю эту порцию, подожди немного.")
        return
    _active_food_requests.add(user_id)
    try:
        await _run_quick_food(callback, state)
    finally:
        _active_food_requests.discard(user_id)


async def _run_quick_food(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != QuickInputState.choose.state:
        await callback.answer("Этот ввод уже обрабатывается или завершён.")
        return
    data = await state.get_data()
    text = str(data.get("quick_text") or "").strip()
    if not text:
        await callback.answer("Сообщение потерялось, пришли ещё раз.", show_alert=True)
        await state.clear()
        return
    if not ai_enabled():
        await callback.answer("Распознавание пока выключено.", show_alert=True)
        return
    await state.set_state(QuickInputState.working)
    await replace_text_view(callback.message, "⏳ Селин разбирается с описанием еды…")
    await callback.answer()
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        allowed = await reserve_ai_call(session, profile.id, "vision")
        await session.commit()
    if not allowed:
        await state.clear()
        await replace_text_view(callback.message, "На сегодня исчерпан защитный лимит распознаваний.")
        return
    try:
        result = normalize_food_result(await analyze_food_text(text))
    except Exception as exc:
        print("[QUICK_FOOD] text analysis failed:", repr(exc))
        await state.clear()
        await replace_text_view(callback.message, "Не получилось оценить по описанию. Попробуй уточнить состав, объём или данные с этикетки.")
        return
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        analysis = AIImageAnalysis(
            user_id=profile.id,
            analysis_type="food",
            telegram_file_id="text_only",
            result={**result, "user_comment": text, "image_count": 0},
            status="pending",
        )
        session.add(analysis)
        await session.flush()
        analysis_id = analysis.id
        await session.commit()
    await state.clear()
    markup = (
        food_category_menu(analysis_id) if result.get("category") == "unknown"
        else food_analysis_menu(analysis_id, can_enter_grams=can_scale_by_grams(result))
    )
    result["user_comment"] = text
    result["image_count"] = 0
    await replace_text_view(
        callback.message,
        "📝 Оценка по описанию (не по фото).\n\n"
        + _food_result_text(result)
        + "\n\nПроверь категорию и размер порции перед записью.",
        reply_markup=markup,
    )


@router.message(QuickInputState.choose, F.text)
async def quick_replace_text(message: Message, state: FSMContext) -> None:
    await state.update_data(quick_text=(message.text or "")[:2000])
    await message.answer("Хорошо, беру последнее сообщение. Что с ним сделать?", reply_markup=intent_keyboard())
