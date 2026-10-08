from __future__ import annotations

import asyncio
from io import BytesIO
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
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
from app.services.challenge_feedback import check_and_notify_challenges
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
from app.services.hydration import record_hydration
from app.services.intraday_coach import create_intraday_food_feedback

router = Router()
TZ = ZoneInfo(settings.timezone)


class PhotoAnalyzeState(StatesGroup):
    waiting_photo = State()
    waiting_kind = State()
    waiting_more_food_photo = State()
    waiting_food_comment = State()
    waiting_food_grams = State()


MAX_FOOD_PHOTOS = 8
MEDIA_GROUP_DEBOUNCE_SECONDS = 0.8


# Telegram sends an album as several independent updates.  Without a tiny
# debounce every photo can race the FSM and create its own "Что на
# изображении?" message.  One media group is one food portion for this bot,
# so collect the whole album first and only then open the analysis wizard.
_media_groups: dict[tuple[int, int, str], dict] = {}
_media_groups_lock = asyncio.Lock()


def now_local() -> datetime:
    return datetime.now(TZ)


async def _send_flow_message(message: Message, state: FSMContext, text: str, *, reply_markup=None) -> None:
    sent = await message.answer(text, reply_markup=reply_markup)
    await state.update_data(photo_flow_message_id=sent.message_id)


async def _edit_flow_message(message: Message, state: FSMContext, text: str, *, reply_markup=None) -> None:
    """Keep the photo-analysis wizard in one Telegram message.

    User photos/comments remain in chat, but service prompts replace each
    other instead of producing a long chain of buttons.
    """

    data = await state.get_data()
    message_id = data.get("photo_flow_message_id")
    if message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=int(message_id),
                text=text,
                reply_markup=reply_markup,
            )
            return
        except TelegramBadRequest as exc:
            # "message is not modified" is harmless; for any other edit
            # limitation fall back to a fresh wizard message.
            if "message is not modified" in str(exc).lower():
                return
        except Exception:
            pass
    await _send_flow_message(message, state, text, reply_markup=reply_markup)


async def _move_flow_message_below_user(message: Message, state: FSMContext, text: str, *, reply_markup=None) -> None:
    """Recreate the single wizard message after the newest user photo/comment.

    Editing the old prompt keeps it chronologically above the photo that was just
    sent, which is easy to miss on mobile. Delete the old prompt and recreate one
    control message below the user's latest content.
    """
    await _delete_flow_message(message, state)
    await _send_flow_message(message, state, text, reply_markup=reply_markup)


async def _delete_flow_message(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    message_id = data.get("photo_flow_message_id")
    if not message_id:
        return
    try:
        await message.bot.delete_message(message.chat.id, int(message_id))
    except Exception:
        try:
            await message.bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=int(message_id),
                reply_markup=None,
            )
        except Exception:
            pass


async def _finish_result_message(message: Message, text: str) -> None:
    """Close inline buttons without creating another reaction message when possible."""
    suffix = f"\n\n✅ {text}"
    try:
        if message.photo:
            base = message.caption or ""
            combined = base + suffix
            if len(combined) <= 1000:
                await message.edit_caption(caption=combined, reply_markup=None)
                return
        elif message.text:
            combined = message.text + suffix
            if len(combined) <= 3900:
                await message.edit_text(combined, reply_markup=None)
                return
        await message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await message.answer(text)


def _merge_comment(existing: str, captions: list[str]) -> str:
    parts = [existing.strip()] if existing and existing.strip() else []
    for caption in captions:
        caption = caption.strip()
        if caption and caption not in parts:
            parts.append(caption)
    return "\n".join(parts).strip()


async def _finalize_media_group(key: tuple[int, int, str]) -> None:
    await asyncio.sleep(MEDIA_GROUP_DEBOUNCE_SECONDS)
    async with _media_groups_lock:
        payload = _media_groups.pop(key, None)
    if not payload:
        return

    message: Message = payload["message"]
    state: FSMContext = payload["state"]
    mode = str(payload.get("mode") or "initial")
    new_ids = list(payload.get("file_ids") or [])[:MAX_FOOD_PHOTOS]
    captions = list(payload.get("captions") or [])
    if not new_ids:
        return

    data = await state.get_data()
    if mode == "append_food":
        existing = list(data.get("photo_file_ids") or [])
        room = max(0, MAX_FOOD_PHOTOS - len(existing))
        existing.extend(new_ids[:room])
        comment = _merge_comment(str(data.get("food_comment") or ""), captions)
        await state.update_data(
            photo_file_ids=existing,
            photo_mimes=["image/jpeg"] * len(existing),
            food_comment=comment,
            selected_kind="food",
        )
        await state.set_state(PhotoAnalyzeState.waiting_kind)
        await _move_flow_message_below_user(
            message,
            state,
            f"В одной порции собрано {len(existing)} фото. Можно сразу считать или добавить комментарий к составу/весу.",
            reply_markup=food_photo_prepare_menu(
                len(existing), has_comment=bool(comment), max_photos=MAX_FOOD_PHOTOS
            ),
        )
        return

    comment = _merge_comment("", captions)
    await state.update_data(
        photo_file_ids=new_ids,
        photo_mimes=["image/jpeg"] * len(new_ids),
        food_comment=comment,
    )
    await state.set_state(PhotoAnalyzeState.waiting_kind)
    await _move_flow_message_below_user(
        message,
        state,
        f"Что на изображениях? В наборе {len(new_ids)} фото.",
        reply_markup=photo_kind_menu(),
    )


async def _queue_media_group(message: Message, state: FSMContext, *, mode: str) -> bool:
    if not message.media_group_id:
        return False
    key = (message.chat.id, message.from_user.id, str(message.media_group_id))
    async with _media_groups_lock:
        payload = _media_groups.get(key)
        if payload is None:
            payload = {
                "message": message,
                "state": state,
                "mode": mode,
                "file_ids": [],
                "captions": [],
                "task": None,
            }
            _media_groups[key] = payload
        payload["message"] = message  # keep the last album item so controls appear below the album
        payload["file_ids"].append(message.photo[-1].file_id)
        if message.caption:
            payload["captions"].append(message.caption)
        task = payload.get("task")
        if task and not task.done():
            task.cancel()
        payload["task"] = asyncio.create_task(_finalize_media_group(key))
    return True


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
        "treat": "вкусняшка",
        "drink": "напиток",
        "unknown": "неясно",
    }
    lines = [
        f"Похоже на: {result.get('summary', 'не получилось уверенно определить')}",
        f"Что вижу: {items}",
        f"Категория: {category_labels.get(result.get('category'), 'неясно')}",
    ]
    if result.get("user_comment"):
        lines.append(f"Учла комментарий: {result['user_comment']}")
    lines.extend([
        "",
        f"Источник КБЖУ: {_nutrition_source_label(str(result.get('nutrition_source') or 'none'))}",
    ])

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
        await _edit_flow_message(message, state, "Я потеряла изображение. Пришли его ещё раз.")
        await state.clear()
        return

    if not await _reserve_vision_call(user, message):
        await _delete_flow_message(message, state)
        await state.clear()
        return

    count = min(len(file_ids), MAX_FOOD_PHOTOS if kind == "food" else 1)
    await _edit_flow_message(
        message,
        state,
        "Смотрю изображение…" if count == 1 else f"Смотрю изображения: {count} шт.…",
    )
    try:
        images: list[tuple[bytes, str]] = []
        for index, file_id in enumerate(file_ids[:count]):
            mime = mime_types[index] if index < len(mime_types) else "image/jpeg"
            images.append((await _download_telegram_photo(message.bot, file_id), mime))

        if kind == "food":
            comment = str(data.get("food_comment") or "").strip()
            result = normalize_food_result(await analyze_food_images(images, user_comment=comment))
            result["user_comment"] = comment
        else:
            result = await analyze_activity_image(images[0][0], images[0][1])
    except Exception as exc:
        print("AI image analysis error:", repr(exc))
        await _edit_flow_message(
            message,
            state,
            "Не получилось распознать изображение. Попробуй ещё раз позже или отметь вручную.",
        )
        await state.clear()
        return

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, user.id, user.full_name)
        row = AIImageAnalysis(
            user_id=profile.id,
            analysis_type=kind,
            telegram_file_id=file_ids[0],
            result={**result, "image_count": count},
            status="pending",
        )
        session.add(row)
        await session.flush()
        analysis_id = row.id
        await session.commit()

    # The wizard itself is disposable.  Keep the user's album and the useful
    # result, not every intermediate button screen.
    await _delete_flow_message(message, state)

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
    await _send_flow_message(
        message,
        state,
        f"Пришли фото еды или скрин из часов / Google Fit. Для еды удобнее отправить одним альбомом до {MAX_FOOD_PHOTOS} фото: блюдо + ингредиенты/упаковки. Подпись к альбому станет комментарием для анализа.",
        reply_markup=back_menu("photoai:cancel", "❌ Отмена"),
    )


@router.message(PhotoAnalyzeState.waiting_photo, F.photo)
async def photo_received_after_prompt(message: Message, state: FSMContext) -> None:
    if await _queue_media_group(message, state, mode="initial"):
        return
    photo = message.photo[-1]
    await state.update_data(
        photo_file_ids=[photo.file_id],
        photo_mimes=["image/jpeg"],
        food_comment=(message.caption or "").strip(),
    )
    await state.set_state(PhotoAnalyzeState.waiting_kind)
    await _move_flow_message_below_user(message, state, "Что на изображении?", reply_markup=photo_kind_menu())


@router.message(PhotoAnalyzeState.waiting_more_food_photo, F.photo)
async def more_food_photo_received(message: Message, state: FSMContext) -> None:
    if await _queue_media_group(message, state, mode="append_food"):
        return
    data = await state.get_data()
    file_ids = list(data.get("photo_file_ids") or [])
    mime_types = list(data.get("photo_mimes") or [])
    if len(file_ids) >= MAX_FOOD_PHOTOS:
        await state.set_state(PhotoAnalyzeState.waiting_kind)
        await _edit_flow_message(
            message,
            state,
            f"Уже собрано {MAX_FOOD_PHOTOS} фото — этого хватит даже для очень сложной лепёшки.",
            reply_markup=food_photo_prepare_menu(
                len(file_ids),
                has_comment=bool(str(data.get("food_comment") or "").strip()),
                max_photos=MAX_FOOD_PHOTOS,
            ),
        )
        return

    file_ids.append(message.photo[-1].file_id)
    mime_types.append("image/jpeg")
    comment = str(data.get("food_comment") or "").strip()
    caption = (message.caption or "").strip()
    if caption:
        comment = f"{comment}\n{caption}".strip()
    await state.update_data(
        photo_file_ids=file_ids,
        photo_mimes=mime_types,
        selected_kind="food",
        food_comment=comment,
    )
    await state.set_state(PhotoAnalyzeState.waiting_kind)
    await _move_flow_message_below_user(
        message,
        state,
        f"В одной порции собрано {len(file_ids)} фото. Можно считать или добавить комментарий.",
        reply_markup=food_photo_prepare_menu(
            len(file_ids),
            has_comment=bool(comment),
            max_photos=MAX_FOOD_PHOTOS,
        ),
    )


@router.message(PhotoAnalyzeState.waiting_food_comment, F.text)
async def food_comment_received(message: Message, state: FSMContext) -> None:
    comment = (message.text or "").strip()
    if not comment:
        await message.answer("Напиши комментарий текстом.")
        return
    data = await state.get_data()
    await state.update_data(food_comment=comment, selected_kind="food")
    await state.set_state(PhotoAnalyzeState.waiting_kind)
    count = len(data.get("photo_file_ids") or [])
    await _move_flow_message_below_user(
        message,
        state,
        "Комментарий сохранила. Он будет важнее моих догадок по фото.",
        reply_markup=food_photo_prepare_menu(count, has_comment=True, max_photos=MAX_FOOD_PHOTOS),
    )


@router.message(PhotoAnalyzeState.waiting_kind, F.text)
async def food_comment_without_button(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("selected_kind") != "food":
        return
    comment = (message.text or "").strip()
    if not comment:
        return
    await state.update_data(food_comment=comment)
    count = len(data.get("photo_file_ids") or [])
    await _move_flow_message_below_user(
        message,
        state,
        "Приняла это как комментарий к еде.",
        reply_markup=food_photo_prepare_menu(count, has_comment=True, max_photos=MAX_FOOD_PHOTOS),
    )


@router.message(F.photo)
async def unsolicited_photo(message: Message, state: FSMContext) -> None:
    # This router is included after the wardrobe/gacha router, so its own
    # image-upload FSM has first chance to consume generated outfit art.
    if not ai_enabled():
        return

    current_state = await state.get_state()
    data = await state.get_data()
    existing = list(data.get("photo_file_ids") or [])

    if message.media_group_id:
        mode = "append_food" if data.get("selected_kind") == "food" and existing else "initial"
        await _queue_media_group(message, state, mode=mode)
        return

    # Telegram albums arrive as several independent photo updates. If the user
    # sends an album while we are already preparing an analysis, keep appending
    # instead of replacing the previous image. This also makes the repeated
    # "Добавить фото" flow tolerant of sending several images at once.
    if current_state == PhotoAnalyzeState.waiting_kind.state and existing:
        if len(existing) >= MAX_FOOD_PHOTOS:
            await _edit_flow_message(
                message,
                state,
                f"Уже собрано {MAX_FOOD_PHOTOS} фото — больше в один анализ не беру.",
                reply_markup=food_photo_prepare_menu(
                    len(existing),
                    has_comment=bool(str(data.get("food_comment") or "").strip()),
                    max_photos=MAX_FOOD_PHOTOS,
                ),
            )
            return
        mimes = list(data.get("photo_mimes") or [])
        existing.append(message.photo[-1].file_id)
        mimes.append("image/jpeg")
        comment = str(data.get("food_comment") or "").strip()
        caption = (message.caption or "").strip()
        if caption:
            comment = f"{comment}\n{caption}".strip()
        await state.update_data(photo_file_ids=existing, photo_mimes=mimes, food_comment=comment)
        selected_kind = data.get("selected_kind")
        if selected_kind == "food":
            markup = food_photo_prepare_menu(
                len(existing), has_comment=bool(comment), max_photos=MAX_FOOD_PHOTOS
            )
            await _move_flow_message_below_user(
                message,
                state,
                f"В одной порции собрано {len(existing)} фото. Можно считать или добавить комментарий.",
                reply_markup=markup,
            )
        else:
            await _move_flow_message_below_user(
                message,
                state,
                f"Добавила ещё фото ({len(existing)}). Если это еда — проанализирую их вместе.",
                reply_markup=photo_kind_menu(),
            )
        return

    photo = message.photo[-1]
    await state.update_data(
        photo_file_ids=[photo.file_id],
        photo_mimes=["image/jpeg"],
        food_comment=(message.caption or "").strip(),
    )
    await state.set_state(PhotoAnalyzeState.waiting_kind)
    await _send_flow_message(
        message,
        state,
        "Хочешь, чтобы я распознала это фото?",
        reply_markup=photo_kind_menu(),
    )


@router.callback_query(F.data == "photoai:cancel")
async def photo_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await _delete_flow_message(callback.message, state)
    await state.clear()
    await callback.answer()


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
        count = len(data.get("photo_file_ids") or [])
        await _edit_flow_message(
            callback.message,
            state,
            f"Одна порция = один набор. Можно добавить ещё фото упаковок/ингредиентов (до {MAX_FOOD_PHOTOS}) и комментарий: что внутри, сколько граммов, было ли масло/соус и т.д.",
            reply_markup=food_photo_prepare_menu(
                count,
                has_comment=bool(str(data.get("food_comment") or "").strip()),
                max_photos=MAX_FOOD_PHOTOS,
            ),
        )
        return

    await _analyze_from_state(callback.message, callback.from_user, state, kind="activity")


@router.callback_query(F.data == "photoai:food:add_photo")
async def add_more_food_photo(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    file_ids = list(data.get("photo_file_ids") or [])
    if not file_ids:
        await callback.answer("Первое фото потерялось. Пришли его ещё раз.", show_alert=True)
        await state.clear()
        return
    if len(file_ids) >= MAX_FOOD_PHOTOS:
        await callback.answer(f"Лимит — {MAX_FOOD_PHOTOS} фото на один расчёт.", show_alert=True)
        return
    await state.set_state(PhotoAnalyzeState.waiting_more_food_photo)
    await callback.answer()
    await _edit_flow_message(
        callback.message,
        state,
        f"Пришли ещё фото или целый альбом. Сейчас {len(file_ids)}/{MAX_FOOD_PHOTOS}. Всё, что пришлёшь одним набором, считаю одной порцией.",
        reply_markup=back_menu("photoai:cancel", "❌ Отмена"),
    )


@router.callback_query(F.data == "photoai:food:comment")
async def add_food_comment(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("photo_file_ids"):
        await callback.answer("Фото потерялось. Пришли его ещё раз.", show_alert=True)
        await state.clear()
        return
    await state.set_state(PhotoAnalyzeState.waiting_food_comment)
    await callback.answer()
    await _edit_flow_message(
        callback.message,
        state,
        "Напиши всё, что знаешь: состав, граммы, сколько чего положила, масло/соус, КБЖУ с упаковки. Можно обычным человеческим текстом.",
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


@router.callback_query(F.data.regexp(r"^photoai:food:(meal|snack|treat|drink):\d+$"))
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
    try:
        await callback.message.edit_reply_markup(reply_markup=food_category_menu(analysis_id))
    except Exception:
        await callback.message.answer("Как это записать?", reply_markup=food_category_menu(analysis_id))


@router.callback_query(F.data.startswith("photoai:foodcat:"))
async def set_food_category(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        return
    category = parts[2]
    analysis_id = int(parts[3])
    if category not in {"meal", "snack", "treat", "drink"}:
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
    try:
        await callback.message.edit_reply_markup(
            reply_markup=food_analysis_menu(analysis_id, can_enter_grams=can_scale_by_grams(result))
        )
    except Exception:
        await callback.message.answer(
            "Теперь выбери, сколько из этой порции записать.",
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
        if category not in {"meal", "snack", "treat", "drink"}:
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
        elif category == "treat":
            stat.snacks += 1
            await apply_reward(session, profile, event_type="treat_logged", payload=payload)
        else:
            stat.drinks += 1
            drink_kind = result.get("drink_kind")
            if drink_kind == "plain":
                stat.plain_drinks += 1
                hydration_rewarded = await record_hydration(
                    session, profile, stat, source="photo_plain_drink"
                )
                if hydration_rewarded:
                    reward_text = "+3 XP · +1 связь с Тори за гидратацию"
            elif drink_kind == "energy":
                stat.energy_drinks += 1
                await apply_reward(
                    session, profile, event_type="drink_ai", payload={**payload, "kind": drink_kind}
                )
            else:
                stat.caloric_drinks += 1
                await apply_reward(
                    session, profile, event_type="drink_ai", payload={**payload, "kind": drink_kind}
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

    labels = {"meal": "полноценную еду", "snack": "перекус", "treat": "вкусняшку", "drink": "напиток"}
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
            if result.get("category") not in {"meal", "snack", "treat", "drink"}:
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
        if result.get("category") not in {"meal", "snack", "treat", "drink"}:
            await callback.answer()
            await callback.message.answer("Сначала уточни категорию.", reply_markup=food_category_menu(analysis_id))
            return

    text = await _record_food_choice(callback, analysis_id=analysis_id, fraction=fraction, portion_label=label)
    await callback.answer()
    if text:
        await _finish_result_message(callback.message, text)
        await check_and_notify_challenges(callback.message, callback.from_user.id, callback.from_user.full_name)
        advice = await create_intraday_food_feedback(
            callback.from_user.id,
            callback.from_user.full_name,
            latest_action="Записана новая порция еды или напитка через фото. Оцени, нужно ли скорректировать оставшуюся часть дня.",
        )
        if advice:
            await send_reaction(
                callback.message,
                "selin",
                advice.get("emotion", "neutral"),
                "🧭 Корректировка маршрута\n\n" + advice["text"],
            )
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
        await check_and_notify_challenges(message, message.from_user.id, message.from_user.full_name)
        advice = await create_intraday_food_feedback(
            message.from_user.id,
            message.from_user.full_name,
            latest_action="Записана порция еды или напитка через фото с указанным весом. Оцени, нужно ли скорректировать оставшуюся часть дня.",
        )
        if advice:
            await send_reaction(
                message,
                "selin",
                advice.get("emotion", "neutral"),
                "🧭 Корректировка маршрута\n\n" + advice["text"],
            )


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
    await _finish_result_message(callback.message, text)
    await check_and_notify_challenges(callback.message, callback.from_user.id, callback.from_user.full_name)


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
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
