from __future__ import annotations

from io import BytesIO
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, User

from app.config import settings
from app.db import SessionLocal
from app.keyboards import (
    activity_analysis_menu,
    back_menu,
    food_analysis_menu,
    food_category_menu,
    food_photo_prepare_menu,
    photo_kind_menu,
)
from app.models import AIImageAnalysis
from app.services.ai_engine import ai_enabled, analyze_activity_image, analyze_food_images
from app.services.ai_features import reserve_ai_call
from app.services.assets import send_reaction
from app.services.nutrition import (
    can_scale_by_grams,
    normalize_food_result,
    record_food_nutrition,
    scaled_nutrition,
)
from app.services.rewards import apply_reward, get_or_create_daily, get_or_create_profile
from app.services.self_workouts import (
    manual_reward_already_claimed,
    mark_manual_reward_claimed,
    reward_for_minutes,
)
from app.services.steps import evaluate_steps

router = Router()
TZ = ZoneInfo(settings.timezone)


class PhotoAnalyzeState(StatesGroup):
    waiting_photo = State()
    waiting_kind = State()
    waiting_second_food_photo = State()
    waiting_food_grams = State()


def now_local() -> datetime:
    return datetime.now(TZ)


def _confidence_label(value: str) -> str:
    return {"low": "низкая", "medium": "средняя", "high": "высокая"}.get(value, value or "низкая")


def _nutrition_source_label(value: str) -> str:
    return {
        "label": "по этикетке / КБЖУ",
        "mixed": "по этикетке + фото блюда",
        "photo_estimate": "примерная оценка по фото",
        "none": "не определён",
    }.get(value, "не определён")


def _fmt_number(value: float | int | None, suffix: str = "") -> str:
    if value is None:
        return "—"
    number = float(value)
    if abs(number - round(number)) < 0.05:
        text = str(int(round(number)))
    else:
        text = f"{number:.1f}"
    return f"{text}{suffix}"


def _food_result_text(result: dict) -> str:
    result = normalize_food_result(result)
    items = ", ".join(result.get("items") or []) or "не уверена"
    category_labels = {
        "meal": "полноценная еда",
        "snack": "перекус",
        "drink": "напиток",
        "unknown": "неясно",
    }
    lines = [
        f"Похоже на: {result.get('summary', 'не получилось уверенно определить')}",
        f"Что вижу: {items}",
        f"Категория: {category_labels.get(result.get('category'), 'неясно')}",
        "",
        f"Источник КБЖУ: {_nutrition_source_label(str(result.get('nutrition_source') or 'none'))}",
    ]

    kcal = result.get("calories_kcal")
    protein = result.get("protein_g")
    fat = result.get("fat_g")
    carbs = result.get("carbs_g")
    if any(v is not None for v in (kcal, protein, fat, carbs)):
        prefix = "≈ " if result.get("nutrition_source") == "photo_estimate" else ""
        lines.append(f"🔥 {prefix}{_fmt_number(kcal, ' ккал')}")
        lines.append(
            f"Б {_fmt_number(protein, ' г')} · Ж {_fmt_number(fat, ' г')} · У {_fmt_number(carbs, ' г')}"
        )
        reference_grams = result.get("reference_grams")
        if reference_grams is not None:
            lines.append(f"Вес расчёта: {_fmt_number(reference_grams, ' г')}")
        low = result.get("calories_min_kcal")
        high = result.get("calories_max_kcal")
        if low is not None and high is not None and result.get("nutrition_source") == "photo_estimate":
            lines.append(f"Примерный диапазон: {low}–{high} ккал")

        p100 = result.get("per_100g_protein_g")
        f100 = result.get("per_100g_fat_g")
        c100 = result.get("per_100g_carbs_g")
        k100 = result.get("per_100g_calories_kcal")
        if result.get("nutrition_source") in {"label", "mixed"} and any(
            v is not None for v in (k100, p100, f100, c100)
        ):
            lines.append(
                "На 100 г: "
                f"{_fmt_number(k100, ' ккал')} · Б {_fmt_number(p100)} · Ж {_fmt_number(f100)} · У {_fmt_number(c100)}"
            )
    else:
        lines.append("КБЖУ по этому изображению определить не получилось.")

    if result.get("basis"):
        lines.append(f"Расчёт: {result['basis']}")
    lines.append(f"Уверенность: {_confidence_label(str(result.get('confidence') or 'low'))}")
    if result.get("note"):
        lines.append(str(result["note"]))
    lines.append("\nНичего не запишу, пока ты сама не подтвердишь порцию.")
    return "\n".join(lines)


async def _download_telegram_photo(bot, file_id: str) -> bytes:
    buf = BytesIO()
    tg_file = await bot.get_file(file_id)
    await bot.download_file(tg_file.file_path, destination=buf)
    return buf.getvalue()


async def _reserve_vision_call(user: User, message: Message) -> bool:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, user.id, user.full_name)
        allowed = await reserve_ai_call(session, profile.id, "vision")
        await session.commit()
    if not allowed:
        await message.answer("На сегодня достигнут защитный лимит AI-запросов.")
    return allowed


async def _analyze_from_state(message: Message, user: User, state: FSMContext, *, kind: str) -> None:
    data = await state.get_data()
    file_ids = list(data.get("photo_file_ids") or [])
    mime_types = list(data.get("photo_mimes") or [])
    if not file_ids:
        await message.answer("Я потеряла изображение. Пришли его ещё раз.")
        await state.clear()
        return

    if not await _reserve_vision_call(user, message):
        await state.clear()
        return

    await message.answer("Смотрю изображение…" if len(file_ids) == 1 else "Смотрю оба изображения…")
    try:
        images: list[tuple[bytes, str]] = []
        for index, file_id in enumerate(file_ids[:2]):
            mime = mime_types[index] if index < len(mime_types) else "image/jpeg"
            images.append((await _download_telegram_photo(message.bot, file_id), mime))

        if kind == "food":
            result = normalize_food_result(await analyze_food_images(images))
        else:
            result = await analyze_activity_image(images[0][0], images[0][1])
    except Exception as exc:
        print("AI image analysis error:", repr(exc))
        await message.answer("Не получилось распознать изображение. Попробуй ещё раз позже или отметь вручную.")
        await state.clear()
        return

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, user.id, user.full_name)
        row = AIImageAnalysis(
            user_id=profile.id,
            analysis_type=kind,
            telegram_file_id=file_ids[0],
            result={**result, "image_count": len(file_ids)},
            status="pending",
        )
        session.add(row)
        await session.flush()
        analysis_id = row.id
        await session.commit()

    if kind == "food":
        if result.get("category") == "unknown":
            await send_reaction(
                message,
                "tori",
                "curious",
                _food_result_text(result) + "\n\nСначала уточни, чем это считать.",
                reply_markup=food_category_menu(analysis_id),
            )
        else:
            await send_reaction(
                message,
                "tori",
                "curious",
                _food_result_text(result),
                reply_markup=food_analysis_menu(analysis_id, can_enter_grams=can_scale_by_grams(result)),
            )
    else:
        pieces = []
        if result.get("steps") is not None:
            pieces.append(f"🚶 Шаги: {result['steps']}")
        if result.get("workout_minutes") is not None:
            pieces.append(f"🏋️ Тренировка: {result['workout_minutes']} мин")
        if result.get("active_minutes") is not None:
            pieces.append(f"⏱ Активные минуты: {result['active_minutes']}")
        if result.get("active_calories") is not None:
            pieces.append(f"🔥 Активные ккал: {result['active_calories']}")
        text = f"Распознала: {result.get('summary', '')}\n\n" + ("\n".join(pieces) if pieces else "Цифры прочитать не получилось.")
        text += f"\n\nУверенность: {_confidence_label(str(result.get('confidence') or 'low'))}\nПроверь цифры перед записью."
        await send_reaction(message, "selin", "curious", text, reply_markup=activity_analysis_menu(analysis_id))

    await state.clear()


@router.message(F.text == "📷 Анализ фото")
async def photo_analysis_start(message: Message, state: FSMContext) -> None:
    if not ai_enabled():
        await message.answer("Анализ фото пока выключен: на сервере не задан OPENAI_API_KEY.")
        return
    await state.clear()
    await state.set_state(PhotoAnalyzeState.waiting_photo)
    await message.answer(
        "Пришли фото еды или скрин из часов / Google Fit. Для еды потом можно добавить второе фото — например этикетку с КБЖУ.",
        reply_markup=back_menu("photoai:cancel", "❌ Отмена"),
    )


@router.message(PhotoAnalyzeState.waiting_photo, F.photo)
async def photo_received_after_prompt(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1]
    await state.update_data(photo_file_ids=[photo.file_id], photo_mimes=["image/jpeg"])
    await state.set_state(PhotoAnalyzeState.waiting_kind)
    await message.answer("Что на изображении?", reply_markup=photo_kind_menu())


@router.message(PhotoAnalyzeState.waiting_second_food_photo, F.photo)
async def second_food_photo_received(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    file_ids = list(data.get("photo_file_ids") or [])
    mime_types = list(data.get("photo_mimes") or [])
    if not file_ids:
        file_ids = [message.photo[-1].file_id]
        mime_types = ["image/jpeg"]
    elif len(file_ids) < 2:
        file_ids.append(message.photo[-1].file_id)
        mime_types.append("image/jpeg")
    else:
        file_ids[1] = message.photo[-1].file_id
        mime_types[1] = "image/jpeg"
    await state.update_data(photo_file_ids=file_ids, photo_mimes=mime_types, selected_kind="food")
    await state.set_state(PhotoAnalyzeState.waiting_kind)
    await message.answer(
        "Добавила второе фото. Теперь могу объединить блюдо и этикетку в один расчёт.",
        reply_markup=food_photo_prepare_menu(has_second_photo=True),
    )


@router.message(F.photo)
async def unsolicited_photo(message: Message, state: FSMContext) -> None:
    # This router is included after the wardrobe/gacha router, so its own
    # image-upload FSM has first chance to consume generated outfit art.
    if not ai_enabled():
        return
    photo = message.photo[-1]
    await state.update_data(photo_file_ids=[photo.file_id], photo_mimes=["image/jpeg"])
    await state.set_state(PhotoAnalyzeState.waiting_kind)
    await message.answer("Хочешь, чтобы я распознала это фото?", reply_markup=photo_kind_menu())


@router.callback_query(F.data == "photoai:cancel")
async def photo_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.answer("Хорошо, не анализирую.")


@router.callback_query(F.data.startswith("photoai:kind:"))
async def choose_photo_kind(callback: CallbackQuery, state: FSMContext) -> None:
    kind = callback.data.rsplit(":", 1)[1]
    if kind not in {"food", "activity"}:
        await callback.answer("Неизвестный тип.", show_alert=True)
        return
    data = await state.get_data()
    if not data.get("photo_file_ids"):
        await callback.answer("Я потеряла изображение. Пришли его ещё раз.", show_alert=True)
        await state.clear()
        return

    await callback.answer()
    if kind == "food":
        await state.update_data(selected_kind="food")
        await state.set_state(PhotoAnalyzeState.waiting_kind)
        await callback.message.answer(
            "Если на другом фото есть упаковка, вес или КБЖУ — добавь его до расчёта. Так будет заметно точнее.",
            reply_markup=food_photo_prepare_menu(has_second_photo=len(data.get("photo_file_ids") or []) >= 2),
        )
        return

    await _analyze_from_state(callback.message, callback.from_user, state, kind="activity")


@router.callback_query(F.data == "photoai:food:add_photo")
async def add_second_food_photo(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("photo_file_ids"):
        await callback.answer("Первое фото потерялось. Пришли его ещё раз.", show_alert=True)
        await state.clear()
        return
    await state.set_state(PhotoAnalyzeState.waiting_second_food_photo)
    await callback.answer()
    await callback.message.answer(
        "Пришли второе фото — например упаковку или этикетку с КБЖУ и весом.",
        reply_markup=back_menu("photoai:cancel", "❌ Отмена"),
    )


@router.callback_query(F.data == "photoai:food:analyze")
async def analyze_food_now(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _analyze_from_state(callback.message, callback.from_user, state, kind="food")


async def _load_pending_analysis(session, user_id: int, analysis_id: int) -> AIImageAnalysis | None:
    row = await session.get(AIImageAnalysis, analysis_id)
    if not row or row.user_id != user_id or row.status != "pending":
        return None
    return row


@router.callback_query(F.data.regexp(r"^photoai:food:(meal|snack|drink):\d+$"))
async def legacy_accept_food_analysis(callback: CallbackQuery, state: FSMContext) -> None:
    """Keep old V7 inline buttons usable after deploying V8."""
    parts = callback.data.split(":")
    category = parts[2]
    analysis_id = int(parts[3])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        row = await _load_pending_analysis(session, profile.id, analysis_id)
        if not row:
            await callback.answer("Это распознавание уже закрыто.", show_alert=True)
            return
        result = dict(row.result or {})
        result["category"] = category
        row.result = result
        await session.commit()
    text = await _record_food_choice(
        callback,
        analysis_id=analysis_id,
        fraction=1.0,
        portion_label="вся порция",
    )
    await state.clear()
    await callback.answer()
    if text:
        await send_reaction(callback.message, "selin", "smirk", text)


@router.callback_query(F.data.startswith("photoai:foodcategory:"))
async def choose_food_category_prompt(callback: CallbackQuery) -> None:
    analysis_id = int(callback.data.rsplit(":", 1)[1])
    await callback.answer()
    await callback.message.answer("Как это записать?", reply_markup=food_category_menu(analysis_id))


@router.callback_query(F.data.startswith("photoai:foodcat:"))
async def set_food_category(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        return
    category = parts[2]
    analysis_id = int(parts[3])
    if category not in {"meal", "snack", "drink"}:
        return

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        row = await _load_pending_analysis(session, profile.id, analysis_id)
        if not row:
            await callback.answer("Это распознавание уже закрыто.", show_alert=True)
            return
        result = dict(row.result or {})
        result["category"] = category
        row.result = result
        await session.commit()

    await callback.answer("Категория изменена")
    await callback.message.answer(
        "Окей. Теперь выбери, сколько из этой порции записать.",
        reply_markup=food_analysis_menu(analysis_id, can_enter_grams=can_scale_by_grams(result)),
    )


async def _record_food_choice(
    callback_or_message: CallbackQuery | Message,
    *,
    analysis_id: int,
    fraction: float = 1.0,
    grams: float | None = None,
    portion_label: str,
) -> str | None:
    if isinstance(callback_or_message, CallbackQuery):
        user = callback_or_message.from_user
    else:
        user = callback_or_message.from_user
    if not user:
        return None

    today = now_local().date()
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, user.id, user.full_name)
        row = await _load_pending_analysis(session, profile.id, analysis_id)
        if not row:
            return "Это распознавание уже закрыто."
        result = normalize_food_result(row.result or {})
        category = result.get("category")
        if category not in {"meal", "snack", "drink"}:
            return "Сначала нужно уточнить категорию."

        stat = await get_or_create_daily(session, profile.id, today)
        nutrition = scaled_nutrition(result, fraction=fraction, grams=grams)
        reward_text = ""
        payload = {
            "analysis_id": row.id,
            "nutrition_source": result.get("nutrition_source"),
            "portion_label": portion_label,
            "portion_grams": nutrition.get("portion_grams"),
            "calories_kcal": nutrition.get("calories_kcal"),
            "protein_g": nutrition.get("protein_g"),
            "fat_g": nutrition.get("fat_g"),
            "carbs_g": nutrition.get("carbs_g"),
        }

        if category == "meal":
            stat.meals += 1
            rewarded = stat.meals <= 3
            if rewarded:
                await apply_reward(
                    session,
                    profile,
                    event_type="meal_complete_ai",
                    xp=20,
                    coins=3,
                    payload=payload,
                )
                reward_text = "+20 XP · +3 монеты"
            else:
                await apply_reward(session, profile, event_type="meal_ai_unrewarded", payload=payload)
        elif category == "snack":
            stat.snacks += 1
            await apply_reward(session, profile, event_type="snack_ai", payload=payload)
        else:
            stat.drinks += 1
            drink_kind = result.get("drink_kind")
            if drink_kind == "plain":
                stat.plain_drinks += 1
            elif drink_kind == "energy":
                stat.energy_drinks += 1
            else:
                stat.caloric_drinks += 1
            await apply_reward(
                session,
                profile,
                event_type="drink_ai",
                payload={**payload, "kind": drink_kind},
            )

        await record_food_nutrition(
            session,
            user_id=profile.id,
            logged_on=today,
            analysis_id=row.id,
            category=category,
            result=result,
            fraction=fraction,
            grams=grams,
            portion_label=portion_label,
        )
        row.status = "accepted"
        row.resolved_at = now_local()
        await session.commit()

    labels = {"meal": "полноценную еду", "snack": "перекус", "drink": "напиток"}
    text = f"Записала как {labels[category]} · {portion_label}."
    if nutrition.get("calories_kcal") is not None:
        text += (
            f"\n🔥 {_fmt_number(nutrition.get('calories_kcal'), ' ккал')}"
            f" · Б {_fmt_number(nutrition.get('protein_g'))}"
            f" · Ж {_fmt_number(nutrition.get('fat_g'))}"
            f" · У {_fmt_number(nutrition.get('carbs_g'))}"
        )
    if reward_text:
        text += f"\n{reward_text}"
    return text


@router.callback_query(F.data.startswith("photoai:portion:"))
async def accept_food_portion(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        return
    mode = parts[2]
    analysis_id = int(parts[3])

    if mode == "grams":
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
            row = await _load_pending_analysis(session, profile.id, analysis_id)
            if not row:
                await callback.answer("Это распознавание уже закрыто.", show_alert=True)
                return
            result = normalize_food_result(row.result or {})
            if result.get("category") not in {"meal", "snack", "drink"}:
                await callback.answer()
                await callback.message.answer("Сначала уточни категорию.", reply_markup=food_category_menu(analysis_id))
                return
            if not can_scale_by_grams(result):
                await callback.answer("Нет данных для пересчёта по граммам.", show_alert=True)
                return
        await state.set_state(PhotoAnalyzeState.waiting_food_grams)
        await state.update_data(nutrition_analysis_id=analysis_id)
        await callback.answer()
        await callback.message.answer(
            "Сколько граммов ты съела? Пришли число, например 140.",
            reply_markup=back_menu("photoai:cancel", "❌ Отмена"),
        )
        return

    fractions = {"full": (1.0, "вся порция"), "half": (0.5, "½ порции"), "third": (1 / 3, "⅓ порции")}
    if mode not in fractions:
        return
    fraction, label = fractions[mode]

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        row = await _load_pending_analysis(session, profile.id, analysis_id)
        if not row:
            await callback.answer("Это распознавание уже закрыто.", show_alert=True)
            return
        result = row.result or {}
        if result.get("category") not in {"meal", "snack", "drink"}:
            await callback.answer()
            await callback.message.answer("Сначала уточни категорию.", reply_markup=food_category_menu(analysis_id))
            return

    text = await _record_food_choice(callback, analysis_id=analysis_id, fraction=fraction, portion_label=label)
    await callback.answer()
    if text:
        await send_reaction(callback.message, "selin", "smirk", text)
    await state.clear()


@router.message(PhotoAnalyzeState.waiting_food_grams)
async def accept_food_grams(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    analysis_id = data.get("nutrition_analysis_id")
    raw = (message.text or "").strip().replace(",", ".")
    try:
        grams = float(raw)
    except ValueError:
        await message.answer("Нужно число в граммах, например 140.")
        return
    if not (1 <= grams <= 5000):
        await message.answer("Введи вес от 1 до 5000 г.")
        return
    if not analysis_id:
        await state.clear()
        await message.answer("Я потеряла расчёт. Пришли фото ещё раз.")
        return

    text = await _record_food_choice(
        message,
        analysis_id=int(analysis_id),
        grams=grams,
        portion_label=f"{_fmt_number(grams, ' г')}",
    )
    await state.clear()
    if text:
        await send_reaction(message, "selin", "smirk", text)


@router.callback_query(F.data.startswith("photoai:activity:accept:"))
async def accept_activity_analysis(callback: CallbackQuery) -> None:
    analysis_id = int(callback.data.rsplit(":", 1)[1])
    today = now_local().date()
    reward_lines = []

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        row = await _load_pending_analysis(session, profile.id, analysis_id)
        if not row:
            await callback.answer("Это распознавание уже закрыто.", show_alert=True)
            return
        result = row.result or {}
        stat = await get_or_create_daily(session, profile.id, today)

        steps = result.get("steps")
        if isinstance(steps, int) and 0 <= steps <= 100000:
            tier = evaluate_steps(steps)
            xp_delta = max(0, tier.xp - stat.step_reward_xp)
            coin_delta = max(0, tier.coins - stat.step_reward_coins)
            stat.steps = steps
            stat.step_tier = tier.key
            stat.step_reward_xp = max(stat.step_reward_xp, tier.xp)
            stat.step_reward_coins = max(stat.step_reward_coins, tier.coins)
            if xp_delta or coin_delta:
                await apply_reward(
                    session,
                    profile,
                    event_type="steps_update_ai",
                    xp=xp_delta,
                    coins=coin_delta,
                    payload={"steps": steps, "tier": tier.key, "analysis_id": row.id},
                )
                reward_lines.append(f"Шаги: +{xp_delta} XP · +{coin_delta} монет")

        minutes = result.get("workout_minutes")
        if isinstance(minutes, int) and 0 < minutes <= 300:
            base_reward = reward_for_minutes(minutes)
            already = await manual_reward_already_claimed(session, profile.id, today)
            rewarded = not already and base_reward.xp > 0
            if rewarded:
                await mark_manual_reward_claimed(session, profile.id, today)
            kind = result.get("workout_type")
            if kind not in {"strength", "pilates", "cardio", "other"}:
                kind = "other"
            await apply_reward(
                session,
                profile,
                event_type="manual_workout_complete",
                xp=base_reward.xp if rewarded else 0,
                coins=base_reward.coins if rewarded else 0,
                payload={
                    "kind": kind,
                    "kind_label": kind,
                    "minutes": minutes,
                    "workout_date": today.isoformat(),
                    "rewarded": rewarded,
                    "analysis_id": row.id,
                    "active_minutes": result.get("active_minutes"),
                    "active_calories": result.get("active_calories"),
                },
            )
            if rewarded:
                reward_lines.append(f"Тренировка: +{base_reward.xp} XP · +{base_reward.coins} монет")

        row.status = "accepted"
        row.resolved_at = now_local()
        await session.commit()

    await callback.answer()
    text = "Записала показатели с изображения."
    if reward_lines:
        text += "\n" + "\n".join(reward_lines)
    await send_reaction(callback.message, "selin", "smirk", text)


@router.callback_query(F.data.startswith("photoai:discard:"))
async def discard_analysis(callback: CallbackQuery) -> None:
    analysis_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        row = await _load_pending_analysis(session, profile.id, analysis_id)
        if row:
            row.status = "discarded"
            row.resolved_at = now_local()
            await session.commit()
    await callback.answer()
    await callback.message.answer("Не записываю.")
