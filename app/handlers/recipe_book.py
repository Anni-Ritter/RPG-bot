from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import FoodNutritionLog, RecipeBatch, SavedRecipe
from app.services.ai_engine import ai_enabled, estimate_saved_recipe
from app.services.ai_features import reserve_ai_call
from app.services.assets import replace_text_view, send_reaction
from app.services.challenge_feedback import check_and_notify_challenges
from app.services.intraday_coach import create_intraday_food_feedback
from app.services.recipe_book import (
    CATEGORIES, latest_batch, recent_food_logs, recipe_from_ai, recipe_from_log,
    recipe_result, record_recipe_serving, user_recipe,
)
from app.services.rewards import get_or_create_profile

router = Router()
TZ = ZoneInfo(settings.timezone)
_busy: set[int] = set()


class RecipeState(StatesGroup):
    waiting_description = State()
    waiting_batch_description = State()
    waiting_batch_count = State()
    waiting_recipe_batch_count = State()
    waiting_rename = State()
    waiting_variation = State()
    waiting_variation_grams = State()
    waiting_preview = State()
    processing = State()


def kb(rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=data)] for label, data in rows])


def nutrition_text(recipe: SavedRecipe | dict) -> str:
    data = recipe if isinstance(recipe, dict) else recipe_result(recipe)
    def fmt(key: str, unit: str = " г") -> str:
        value = data.get(key) if isinstance(data, dict) else getattr(data, key)
        return f"{float(value):g}{unit}" if value is not None else "—"
    return (f"🔥 {fmt('calories_kcal', ' ккал')} · Б {fmt('protein_g')} · "
            f"Ж {fmt('fat_g')} · У {fmt('carbs_g')}")


def recipe_buttons(recipe: SavedRecipe) -> InlineKeyboardMarkup:
    rid = recipe.id
    return kb([
        ("✅ Съела как обычно", f"recipe:use:{rid}:100"),
        ("½ порции", f"recipe:use:{rid}:50"),
        ("✏️ Немного изменила состав", f"recipe:change:{rid}"),
        ("⚖️ Указать граммы", f"recipe:grams:{rid}"),
        ("🍲 Заготовить несколько порций", f"recipe:batch:{rid}"),
        ("✏️ Переименовать", f"recipe:rename:{rid}"),
        (("☆ Убрать из избранного" if recipe.is_favorite else "⭐ В избранное"), f"recipe:fav:{rid}"),
        ("🗑 Удалить из книги", f"recipe:delask:{rid}"),
        ("⬅️ К книге припасов", "recipe:menu"),
    ])


async def _open_recipe(message: Message, user_id: int, recipe_id: int):
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, user_id, None)
        recipe = await user_recipe(session, profile.id, recipe_id)
        if not recipe:
            await replace_text_view(message, "Рецепт не найден.", reply_markup=kb([("⬅️ К книге", "recipe:menu")]))
            return
        stock = await latest_batch(session, profile.id, recipe.id)
        stock_line = f"\n🍲 Заготовка: осталось {stock.remaining_portions:g} порц." if stock else ""
        detail = (
            f"📖 {recipe.name}\n{CATEGORIES.get(recipe.category)} · одна обычная порция\n"
            f"{nutrition_text(recipe)}\n"
            f"Источник: {recipe.nutrition_source} · точность: {recipe.confidence}"
            f"{stock_line}\n\nСостав: {recipe.ingredients or 'пока не добавлен'}\n\n"
            "Если состав сегодня другой, используй «Немного изменила». "
            "Исходный рецепт от этого не изменится."
        )
        await replace_text_view(message, detail[:4000], reply_markup=recipe_buttons(recipe))


async def _book_home(message: Message, user_id: int):
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, user_id, None)
        recipes = list((await session.scalars(select(SavedRecipe).where(
            SavedRecipe.user_id == profile.id,
        ).order_by(SavedRecipe.is_favorite.desc(), SavedRecipe.uses.desc(), SavedRecipe.id.desc()).limit(18))).all())
    rows = [(f"{'⭐ ' if r.is_favorite else ''}{r.name[:42]} · {round(r.calories_kcal) if r.calories_kcal is not None else '?'} ккал",
             f"recipe:open:{r.id}") for r in recipes]
    rows += [
        ("🔁 Повторить последнюю еду одним нажатием", "recipe:last"),
        ("🕘 Сохранить из прошлых записей", "recipe:history"),
        ("📝 Создать по описанию", "recipe:create"),
        ("🍲 Рассчитать кастрюлю на несколько дней", "recipe:batchnew"),
    ]
    await replace_text_view(
        message,
        "📖 Книга припасов\n\nВыбирай привычное блюдо — фото и упаковки больше не нужны. "
        "КБЖУ сохранены с прежней точностью: оценка по фото остаётся оценкой.\n\n"
        + (f"В книге {len(recipes)} блюд. Нажми на любое, чтобы быстро записать." if recipes else
           "Пока пусто. Начни с истории еды — твои старые записи уже подойдут."),
        reply_markup=kb(rows),
    )


@router.message(F.text == "📖 Книга припасов")
async def recipe_book_open(message: Message):
    if message.from_user:
        await _book_home_from_user(message)


async def _book_home_from_user(message: Message):
    sent = await message.answer("📖 Открываю книгу припасов…")
    await _book_home(sent, message.from_user.id)


@router.callback_query(F.data == "recipe:menu")
async def recipe_book_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await _book_home(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("recipe:open:"))
async def recipe_open(callback: CallbackQuery):
    await callback.answer()
    await _open_recipe(callback.message, callback.from_user.id, int(callback.data.rsplit(":", 1)[1]))


@router.callback_query(F.data.regexp(r"^recipe:history(?::\d+)?$"))
async def recipe_history(callback: CallbackQuery):
    offset = int(callback.data.split(":")[2]) if len(callback.data.split(":")) == 3 else 0
    offset = min(max(offset, 0), 300)
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, None)
        logs = list((await session.scalars(select(FoodNutritionLog).where(
            FoodNutritionLog.user_id == profile.id,
        ).order_by(FoodNutritionLog.id.desc()).offset(offset).limit(13))).all())
    more = len(logs) > 12
    rows = [(f"📌 {str(log.description)[:36]} · {round(log.calories_kcal) if log.calories_kcal is not None else '?'} ккал",
             f"recipe:save:{log.id}") for log in logs[:12]]
    if more:
        rows.append(("➡️ Старше", f"recipe:history:{offset + 12}"))
    if offset:
        rows.append(("⬅️ Новее", f"recipe:history:{max(0, offset - 12)}"))
    rows.append(("⬅️ К книге припасов", "recipe:menu"))
    await callback.answer()
    await replace_text_view(callback.message, "🕘 Недавняя еда\n\nНажми на запись, чтобы сохранить её как рецепт. "
                            "Рассчитанные раньше КБЖУ и состав подтянутся автоматически.", reply_markup=kb(rows))


@router.callback_query(F.data.startswith("recipe:save:"))
async def recipe_save_log(callback: CallbackQuery):
    log_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, None)
        recipe = await recipe_from_log(session, profile.id, log_id)
        if not recipe:
            await callback.answer("Запись не найдена.", show_alert=True)
            return
        recipe_id = recipe.id
        await session.commit()
    await callback.answer("Рецепт сохранён в книге!")
    # Photo-analysis results are real events; don't erase their chibi art when
    # navigating to the recipe book from the Save button below them.
    if callback.message.photo:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.message.answer("✅ Рецепт добавлен в книгу припасов.",
            reply_markup=kb([("📖 Открыть рецепт", f"recipe:open:{recipe_id}"),
                             ("⬅️ К книге", "recipe:menu")]))
    else:
        await _open_recipe(callback.message, callback.from_user.id, recipe_id)


@router.callback_query(F.data.startswith("recipe:rename:"))
async def recipe_rename_start(callback: CallbackQuery, state: FSMContext):
    rid = int(callback.data.rsplit(":", 1)[1])
    await state.set_state(RecipeState.waiting_rename)
    await state.update_data(recipe_id=rid)
    await callback.answer()
    await replace_text_view(callback.message, "Как назовём этот рецепт? Напиши новое название сообщением.",
                            reply_markup=kb([("❌ Отмена", "recipe:cancel")]))


@router.message(RecipeState.waiting_rename, F.text)
async def recipe_rename_text(message: Message, state: FSMContext):
    data = await state.get_data()
    name = (message.text or "").strip()
    if not 1 <= len(name) <= 120:
        await message.answer("Название должно быть от 1 до 120 символов.")
        return
    rid = int(data.get("recipe_id", 0))
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, None)
        recipe = await user_recipe(session, profile.id, rid)
        if recipe:
            recipe.name = name
            await session.commit()
    await state.clear()
    await message.answer(f"✅ Рецепт теперь называется «{name}».")


@router.callback_query(F.data.startswith("recipe:fav:"))
async def recipe_favorite(callback: CallbackQuery):
    rid = int(callback.data.rsplit(":", 1)[1])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, None)
        recipe = await user_recipe(session, profile.id, rid)
        if recipe:
            recipe.is_favorite = not recipe.is_favorite
            await session.commit()
    await callback.answer()
    await _open_recipe(callback.message, callback.from_user.id, rid)


@router.callback_query(F.data.startswith("recipe:delask:"))
async def recipe_delete_prompt(callback: CallbackQuery):
    rid = int(callback.data.rsplit(":", 1)[1])
    await callback.answer()
    await replace_text_view(callback.message, "Удалить рецепт из книги? Старые записи питания останутся.",
                            reply_markup=kb([("🗑 Да, удалить", f"recipe:delete:{rid}"), ("Отмена", f"recipe:open:{rid}")]))


@router.callback_query(F.data.startswith("recipe:delete:"))
async def recipe_delete(callback: CallbackQuery):
    rid = int(callback.data.rsplit(":", 1)[1])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, None)
        recipe = await user_recipe(session, profile.id, rid)
        if recipe:
            for batch in (await session.scalars(select(RecipeBatch).where(RecipeBatch.recipe_id == rid, RecipeBatch.user_id == profile.id))).all():
                await session.delete(batch)
            await session.delete(recipe)
            await session.commit()
    await callback.answer("Удалено")
    await _book_home(callback.message, callback.from_user.id)


@router.callback_query(F.data == "recipe:last")
async def repeat_last(callback: CallbackQuery):
    uid = callback.from_user.id
    if uid in _busy:
        await callback.answer("Уже записываю…")
        return
    _busy.add(uid)
    try:
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, uid, callback.from_user.full_name)
            log = (await recent_food_logs(session, profile.id, limit=1))
            if not log:
                await callback.answer("История пока пуста.", show_alert=True)
                return
            recipe = await recipe_from_log(session, profile.id, log[0].id)
            row, text = await record_recipe_serving(session, profile, recipe, datetime.now(TZ).date())
            await session.commit()
        await callback.answer("Записала последнюю еду")
        await replace_text_view(callback.message, text, reply_markup=kb([
            ("📖 К привычному блюду", f"recipe:open:{recipe.id}"), ("⬅️ В книгу", "recipe:menu")]))
        await _after_logging(callback, recipe.name)
    except ValueError as exc:
        await callback.answer(str(exc)[:190], show_alert=True)
    finally:
        _busy.discard(uid)


async def _after_logging(callback: CallbackQuery, name: str) -> None:
    await check_and_notify_challenges(callback.message, callback.from_user.id, callback.from_user.full_name)
    advice = await create_intraday_food_feedback(
        callback.from_user.id, callback.from_user.full_name,
        latest_action=f"Через книгу припасов записан(а): {name}. Использована прежняя точность КБЖУ.")
    if advice:
        await send_reaction(callback.message, "selin", advice.get("emotion", "neutral"),
                            "🧭 Корректировка маршрута\n\n" + advice["text"])


@router.callback_query(F.data.startswith("recipe:use:"))
async def recipe_use(callback: CallbackQuery):
    uid = callback.from_user.id
    if uid in _busy:
        await callback.answer("Уже записываю…")
        return
    _busy.add(uid)
    try:
        _, _, rid, percent = callback.data.split(":")
        fraction = int(percent) / 100
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, uid, callback.from_user.full_name)
            recipe = await user_recipe(session, profile.id, int(rid))
            if not recipe:
                await callback.answer("Рецепт не найден", show_alert=True)
                return
            row, text = await record_recipe_serving(session, profile, recipe, datetime.now(TZ).date(), fraction=fraction)
            recipe_name = recipe.name
            await session.commit()
        await callback.answer("Сохранила!")
        await replace_text_view(callback.message, text, reply_markup=kb([
            ("⬅️ К рецепту", f"recipe:open:{rid}"), ("📖 В книгу", "recipe:menu")]))
        await _after_logging(callback, recipe_name)
    except ValueError as exc:
        await callback.answer(str(exc)[:190], show_alert=True)
    except Exception:
        raise
    finally:
        _busy.discard(uid)


@router.callback_query(F.data.startswith("recipe:grams:"))
async def recipe_grams_start(callback: CallbackQuery, state: FSMContext):
    rid = int(callback.data.rsplit(":", 1)[1])
    await state.set_state(RecipeState.waiting_variation_grams)
    await state.update_data(recipe_id=rid)
    await callback.answer()
    await replace_text_view(callback.message, "Сколько граммов съела? Напиши число. Это работает, только если сохранён вес обычной порции.",
                            reply_markup=kb([("❌ Отмена", "recipe:cancel")]))


@router.message(RecipeState.waiting_variation_grams, F.text)
async def recipe_grams_save(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        grams = float((message.text or "").replace(",", "."))
        if not 1 <= grams <= 5000:
            raise ValueError
    except ValueError:
        await message.answer("Напиши вес в граммах, например 140.")
        return
    rid = int(data["recipe_id"])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        recipe = await user_recipe(session, profile.id, rid)
        if not recipe:
            await state.clear()
            await message.answer("Рецепт не найден.")
            return
        try:
            row, text = await record_recipe_serving(session, profile, recipe, datetime.now(TZ).date(), grams=grams)
        except ValueError as exc:
            await message.answer(str(exc))
            await state.clear()
            return
        await session.commit()
    await state.clear()
    await message.answer(text)
    await check_and_notify_challenges(message, message.from_user.id, message.from_user.full_name)
    advice = await create_intraday_food_feedback(message.from_user.id, message.from_user.full_name,
        latest_action=f"Записана порция {recipe.name} ({grams:g} г) из книги припасов.")
    if advice:
        await send_reaction(message, "selin", advice.get("emotion", "neutral"), "🧭 Корректировка маршрута\n\n" + advice["text"])


@router.callback_query(F.data.startswith("recipe:batch:"))
async def recipe_batch_start(callback: CallbackQuery, state: FSMContext):
    rid = int(callback.data.rsplit(":", 1)[1])
    await state.set_state(RecipeState.waiting_recipe_batch_count)
    await state.update_data(recipe_id=rid)
    await callback.answer()
    await replace_text_view(callback.message, "На сколько готовых порций рассчитана твоя заготовка? "
                            "КБЖУ одной порции уже сохранены — просто укажи количество, например 4.",
                            reply_markup=kb([("❌ Отмена", "recipe:cancel")]))


@router.message(RecipeState.waiting_recipe_batch_count, F.text)
async def recipe_batch_amount(message: Message, state: FSMContext):
    try:
        count = int((message.text or "").strip())
        if not 1 <= count <= 30:
            raise ValueError
    except ValueError:
        await message.answer("Введи целое число от 1 до 30.")
        return
    rid = int((await state.get_data())["recipe_id"])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, None)
        recipe = await user_recipe(session, profile.id, rid)
        if not recipe:
            await message.answer("Рецепт не найден.")
            await state.clear()
            return
        session.add(RecipeBatch(user_id=profile.id, recipe_id=recipe.id,
                                total_portions=count, remaining_portions=count))
        await session.commit()
    await state.clear()
    await message.answer(f"🍲 Заготовка готова: {count} порций «{recipe.name}». Когда записываешь еду через рецепт, запас будет уменьшаться.")


@router.callback_query(F.data == "recipe:create")
async def recipe_create_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(RecipeState.waiting_description)
    await callback.answer()
    await replace_text_view(callback.message,
        "📝 Опиши своё обычное блюдо: ингредиенты, примерные граммы и КБЖУ с этикеток, если есть. "
        "Например: кофе 250 мл, молоко 100 мл, 2 помпы сиропа по 20 ккал. "
        "ИИ один раз посчитает примерную порцию, дальше повторяй без фотографий.",
        reply_markup=kb([("❌ Отмена", "recipe:cancel")]))


@router.callback_query(F.data == "recipe:batchnew")
async def recipe_create_batch_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(RecipeState.waiting_batch_count)
    await callback.answer()
    await replace_text_view(callback.message, "🍲 Сколько порций выйдет из всей готовки? Введи число, например 4.",
                            reply_markup=kb([("❌ Отмена", "recipe:cancel")]))


@router.message(RecipeState.waiting_batch_count, F.text)
async def recipe_new_batch_count(message: Message, state: FSMContext):
    try:
        servings = int((message.text or "").strip())
        if not 1 <= servings <= 30:
            raise ValueError
    except ValueError:
        await message.answer("Введи целое число от 1 до 30.")
        return
    await state.update_data(servings=servings)
    await state.set_state(RecipeState.waiting_batch_description)
    await message.answer("Напиши состав ВСЕЙ кастрюли/сковородки и веса ингредиентов "
                         "(для макарон уточни: сухой или готовый вес). "
                         "ИИ разделит КБЖУ на нужное число порций.")


@router.callback_query(F.data.startswith("recipe:change:"))
async def recipe_change_start(callback: CallbackQuery, state: FSMContext):
    rid = int(callback.data.rsplit(":", 1)[1])
    await state.set_state(RecipeState.waiting_variation)
    await state.update_data(recipe_id=rid)
    await callback.answer()
    await replace_text_view(callback.message,
        "✏️ Что сегодня изменилось? Напиши как обычно: «без моркови, сыра 20 г вместо 30». "
        "Селин пересчитает только этот раз, рецепт останется прежним.",
        reply_markup=kb([("❌ Отмена", "recipe:cancel")]))


async def _estimate_for_state(message: Message, state: FSMContext, *, mode: str):
    data = await state.get_data()
    description = (message.text or "").strip()
    if not 5 <= len(description) <= 4500:
        await message.answer("Опиши состав чуть подробнее (от 5 до 4500 символов).")
        return
    if not ai_enabled():
        await message.answer("ИИ пока отключён. Но из истории еды можно сохранять рецепты вообще без ИИ.")
        await state.clear()
        return

    source = None
    recipe_id = int(data.get("recipe_id") or 0)
    servings = int(data.get("servings") or 1)
    if mode == "variation":
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, message.from_user.id, None)
            original = await user_recipe(session, profile.id, recipe_id)
            if not original:
                await state.clear()
                await message.answer("Рецепт не найден")
                return
            source = recipe_result(original)
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        allowed = await reserve_ai_call(session, profile.id, "chat")
        await session.commit()
    if not allowed:
        await message.answer("Лимит ИИ на сегодня исчерпан.")
        return

    await state.set_state(RecipeState.processing)
    await message.answer("⏳ Селин считает состав. Это разовый анализ — повторения потом будут без ИИ.")
    try:
        estimate = await estimate_saved_recipe(description, servings=servings, original=source)
    except Exception as exc:
        print("Recipe AI calculation failed:", repr(exc))
        prior = {
            "variation": RecipeState.waiting_variation,
            "batch": RecipeState.waiting_batch_description,
            "new": RecipeState.waiting_description,
        }[mode]
        await state.set_state(prior)
        await message.answer("Не удалось посчитать. Попробуй ещё раз или сохрани блюдо из прошлой записи.")
        return
    await state.update_data(estimate=estimate, servings=servings, recipe_mode=mode)
    await state.set_state(RecipeState.waiting_preview)
    caution = str(estimate.get("note") or "Пока приблизительный расчёт")
    await message.answer(
        f"📖 {estimate.get('name', 'Блюдо')} — предварительно на ОДНУ порцию\n"
        f"{nutrition_text(estimate)}\n"
        f"Состав: {estimate.get('ingredients', '')[:450]}\n"
        f"Точность: {estimate.get('confidence', 'low')} · {caution[:300]}\n\n"
        "Подтверждаешь? Если веса были неизвестны, КБЖУ останутся приблизительными.",
        reply_markup=kb(
            [("✅ Записать сегодня с изменениями", "recipe:preview:log"),
             ("⭐ Сохранить как новый вариант", "recipe:preview:save") ,
             ("❌ Отмена", "recipe:cancel")]
            if mode == "variation" else
            [("📖 Сохранить рецепт", "recipe:preview:save"), ("❌ Отмена", "recipe:cancel")]
        ),
    )


@router.message(RecipeState.waiting_description, F.text)
async def recipe_text_new(message: Message, state: FSMContext):
    await _estimate_for_state(message, state, mode="new")


@router.message(RecipeState.waiting_batch_description, F.text)
async def recipe_text_batch(message: Message, state: FSMContext):
    await _estimate_for_state(message, state, mode="batch")


@router.message(RecipeState.waiting_variation, F.text)
async def recipe_text_variation(message: Message, state: FSMContext):
    await _estimate_for_state(message, state, mode="variation")


@router.callback_query(F.data.startswith("recipe:preview:"))
async def recipe_preview_finish(callback: CallbackQuery, state: FSMContext):
    action = callback.data.rsplit(":", 1)[1]
    data = await state.get_data()
    estimate = data.get("estimate")
    if not estimate or (await state.get_state()) != RecipeState.waiting_preview.state:
        await callback.answer("Предпросмотр устарел.", show_alert=True)
        return
    uid = callback.from_user.id
    if uid in _busy:
        await callback.answer("Сохраняю…")
        return
    _busy.add(uid)
    try:
        mode = str(data.get("recipe_mode") or "new")
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, uid, callback.from_user.full_name)
            if action == "save":
                recipe = recipe_from_ai(user_id=profile.id, details=estimate)
                session.add(recipe)
                await session.flush()
                if mode == "batch":
                    servings = int(data.get("servings") or 1)
                    session.add(RecipeBatch(user_id=profile.id, recipe_id=recipe.id,
                                            total_portions=servings, remaining_portions=servings))
                await session.commit()
                text = f"✅ «{recipe.name}» в книге припасов. Можно записывать без фотографий."
                rid = recipe.id
                followup = False
            elif action == "log" and mode == "variation":
                recipe = await user_recipe(session, profile.id, int(data.get("recipe_id") or 0))
                if not recipe:
                    await callback.answer("Исходный рецепт не найден.", show_alert=True)
                    return
                source = recipe_result(recipe)
                override = {**source, **estimate, "nutrition_source": "photo_estimate"}
                row, text = await record_recipe_serving(session, profile, recipe, datetime.now(TZ).date(), override=override)
                await session.commit()
                rid = recipe.id
                followup = True
            else:
                await callback.answer("Неизвестное действие.", show_alert=True)
                return
        await state.clear()
        await callback.answer("Готово")
        await replace_text_view(callback.message, text, reply_markup=kb([
            ("📖 К рецепту", f"recipe:open:{rid}"), ("⬅️ К книге", "recipe:menu")]))
        if followup:
            await _after_logging(callback, recipe.name)
    finally:
        _busy.discard(uid)


@router.callback_query(F.data == "recipe:cancel")
async def recipe_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Отменено")
    await _book_home(callback.message, callback.from_user.id)


@router.message(RecipeState.processing)
async def recipe_busy_message(message: Message):
    await message.answer("Селин ещё считает рецепт. Подожди результата — повторно отправлять не нужно.")
