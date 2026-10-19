"""On-demand analysis of historical nutrition logs, separate from character memory."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db import SessionLocal
from app.services.ai_engine import ai_enabled, generate_nutrition_history_review
from app.services.ai_features import (
    get_calorie_target, get_coach_rules, now_local, reserve_ai_call,
)
from app.services.assets import replace_text_view
from app.services.nutrition_history import PERIODS, build_nutrition_history
from app.services.rewards import get_or_create_profile

router = Router()
_busy: set[int] = set()


def period_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="3 дня", callback_data="nutritionhistory:run:3"),
         InlineKeyboardButton(text="7 дней", callback_data="nutritionhistory:run:7")],
        [InlineKeyboardButton(text="14 дней", callback_data="nutritionhistory:run:14"),
         InlineKeyboardButton(text="30 дней", callback_data="nutritionhistory:run:30")],
    ])


def result_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Другой период", callback_data="nutritionhistory:menu")],
    ])


async def show_period_menu(message: Message) -> None:
    await replace_text_view(
        message,
        "📊 Анализ питания\n\n"
        "Селин посмотрит на записи за последние завершённые дни (сегодня пока не учитываем). "
        "Она найдёт повторяющиеся привычки и предложит, что можно улучшить.\n\n"
        "Неотмеченные блюда считаются неизвестными, а не пропущенными. "
        "Анализ ничего не меняет в статистике и челленджах.\n\nВыбери период:",
        reply_markup=period_menu(),
    )


@router.message(F.text == "📊 Анализ питания")
@router.message(Command("nutrition_history"))
async def nutrition_history_menu(message: Message) -> None:
    sent = await message.answer("📊 Открываю журнал питания…")
    await show_period_menu(sent)


@router.callback_query(F.data == "nutritionhistory:menu")
async def nutrition_history_menu_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_period_menu(callback.message)


@router.callback_query(F.data.startswith("nutritionhistory:run:"))
async def nutrition_history_run(callback: CallbackQuery) -> None:
    try:
        days = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Неизвестный период", show_alert=True)
        return
    if days not in PERIODS:
        await callback.answer("Неизвестный период", show_alert=True)
        return
    user_id = callback.from_user.id
    if user_id in _busy:
        await callback.answer("Селин уже изучает записи. Подожди немного.")
        return
    _busy.add(user_id)
    await callback.answer("Начинаю анализ")
    try:
        await replace_text_view(callback.message, f"⏳ Селин изучает записи за {days} дней…")
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, user_id, callback.from_user.full_name)
            history = await build_nutrition_history(session, profile.id, days=days, today=now_local().date())
            rules = await get_coach_rules(session, profile.id)
            target = await get_calorie_target(session, profile.id)

            if history["recorded_days"] == 0:
                await replace_text_view(
                    callback.message,
                    f"📊 За {days} завершённых дней нет записей еды. "
                    "Мне не из чего делать выводы. Попробуй другой период.",
                    reply_markup=result_menu(),
                )
                return
            if not ai_enabled():
                await replace_text_view(
                    callback.message,
                    "ИИ сейчас не настроен. История питания в базе есть, но анализ Селин недоступен.",
                    reply_markup=result_menu(),
                )
                return
            allowed = await reserve_ai_call(session, profile.id, "review")
            await session.commit()
        if not allowed:
            await replace_text_view(callback.message, "Сегодня лимит AI-вызовов исчерпан. Попробуй позже.", reply_markup=result_menu())
            return
        result = await generate_nutrition_history_review(
            history=history, coach_rules=rules, calorie_target=target,
        )
        report = (result.get("text") or "").strip()
        if not report:
            raise ValueError("Пустой ответ анализа")
        report = report[:3500]
        header = (
            f"📊 Анализ питания · {history['start'].strftime('%d.%m')}–{history['end'].strftime('%d.%m')}\n"
            f"Есть записи за {history['recorded_days']} из {days} дней; "
            f"подробные КБЖУ за {history['nutrition_days']} дней.\n\n"
        )
        if len(header) + len(report) > 3950:
            report = report[:3950-len(header)]
        await replace_text_view(callback.message, header + report, reply_markup=result_menu())
    except Exception as exc:
        print("NUTRITION_HISTORY error:", repr(exc))
        await replace_text_view(callback.message, "Не получилось сделать анализ. Записи не пострадали; попробуй ещё раз.", reply_markup=result_menu())
    finally:
        _busy.discard(user_id)
