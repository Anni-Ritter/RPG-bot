from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AIImageAnalysis, FoodNutritionLog, RecipeBatch, SavedRecipe, UserProfile
from app.services.hydration import record_hydration
from app.services.nutrition import normalize_food_result, record_food_nutrition, scaled_nutrition
from app.services.rewards import apply_reward, get_or_create_daily


CATEGORIES = {"meal": "🍲 Еда", "snack": "🍎 Перекус", "treat": "🍰 Вкусняшка", "drink": "☕ Напиток"}


def _num(value: Any) -> float | None:
    try:
        value = float(value)
        return round(value, 2) if 0 <= value <= 100000 else None
    except (TypeError, ValueError):
        return None


def recipe_result(recipe: SavedRecipe) -> dict[str, Any]:
    return {
        "summary": recipe.name,
        "category": recipe.category,
        "nutrition_source": recipe.nutrition_source,
        "confidence": recipe.confidence,
        "reference_grams": recipe.portion_grams,
        "calories_kcal": recipe.calories_kcal,
        "protein_g": recipe.protein_g,
        "fat_g": recipe.fat_g,
        "carbs_g": recipe.carbs_g,
        "items": [],
        "user_comment": recipe.ingredients,
    }


async def recipe_from_log(session: AsyncSession, user_id: int, log_id: int) -> SavedRecipe | None:
    log = await session.get(FoodNutritionLog, log_id)
    if not log or log.user_id != user_id:
        return None
    existing = await session.scalar(select(SavedRecipe).where(
        SavedRecipe.user_id == user_id, SavedRecipe.source_log_id == log_id
    ))
    if existing:
        return existing
    # Repeating a recipe should not fill the book with an identical new recipe.
    same_name = (await session.scalars(select(SavedRecipe).where(
        SavedRecipe.user_id == user_id,
        SavedRecipe.name == log.description,
        SavedRecipe.category == log.category,
    ))).all()
    for candidate in same_name:
        keys = ("calories_kcal", "protein_g", "fat_g", "carbs_g")
        if all((getattr(candidate, k) is None and getattr(log, k) is None) or
               (getattr(candidate, k) is not None and getattr(log, k) is not None
                and abs(getattr(candidate, k) - getattr(log, k)) < 1.1)
               for k in keys):
            return candidate

    ingredients = ""
    if log.analysis_id:
        analysis = await session.get(AIImageAnalysis, log.analysis_id)
        if analysis and analysis.user_id == user_id:
            raw = analysis.result or {}
            ingredients = str(raw.get("user_comment") or "")
            if not ingredients:
                ingredients = ", ".join(str(item) for item in (raw.get("items") or [])[:12])

    recipe = SavedRecipe(
        user_id=user_id,
        name=str(log.description or "Моё блюдо")[:240],
        category=log.category if log.category in CATEGORIES else "meal",
        ingredients=ingredients[:4000],
        nutrition_source=log.nutrition_source,
        confidence=log.confidence,
        portion_grams=log.portion_grams,
        calories_kcal=log.calories_kcal,
        protein_g=log.protein_g,
        fat_g=log.fat_g,
        carbs_g=log.carbs_g,
        source_log_id=log.id,
    )
    session.add(recipe)
    await session.flush()
    return recipe


async def recent_food_logs(session: AsyncSession, user_id: int, limit: int = 12) -> list[FoodNutritionLog]:
    return list((await session.scalars(select(FoodNutritionLog).where(
        FoodNutritionLog.user_id == user_id,
    ).order_by(FoodNutritionLog.id.desc()).limit(limit))).all())


async def user_recipe(session: AsyncSession, user_id: int, recipe_id: int) -> SavedRecipe | None:
    recipe = await session.get(SavedRecipe, recipe_id)
    return recipe if recipe and recipe.user_id == user_id else None


async def latest_batch(session: AsyncSession, user_id: int, recipe_id: int) -> RecipeBatch | None:
    return await session.scalar(select(RecipeBatch).where(
        RecipeBatch.user_id == user_id,
        RecipeBatch.recipe_id == recipe_id,
        RecipeBatch.remaining_portions > 0,
    ).order_by(RecipeBatch.id).limit(1))


async def record_recipe_serving(
    session: AsyncSession,
    profile: UserProfile,
    recipe: SavedRecipe,
    day,
    *,
    fraction: float = 1.0,
    grams: float | None = None,
    override: dict[str, Any] | None = None,
) -> tuple[FoodNutritionLog, str]:
    """Same rewards, stats, challenges and nutrition-log path as photo logging."""
    if not 0 < fraction <= 5:
        raise ValueError("Неверный размер порции")
    if grams is not None:
        if recipe.portion_grams is None or recipe.portion_grams <= 0:
            raise ValueError("Для этого рецепта не известен вес порции")
        fraction = grams / recipe.portion_grams
        if not 0 < fraction <= 5:
            raise ValueError("Размер порции должен быть от 0 до 5 обычных порций")

    # Treat the variation as one-off. Never silently change the saved recipe.
    result = normalize_food_result(override or recipe_result(recipe))
    stat = await get_or_create_daily(session, profile.id, day)
    category = result.get("category", recipe.category)
    if category not in CATEGORIES:
        category = recipe.category
    batches = list((await session.scalars(select(RecipeBatch).where(
        RecipeBatch.user_id == profile.id,
        RecipeBatch.recipe_id == recipe.id,
        RecipeBatch.remaining_portions > 0,
    ).order_by(RecipeBatch.id))).all())
    stock_total = sum(b.remaining_portions for b in batches)
    if batches and stock_total + 1e-6 < fraction:
        raise ValueError(f"В заготовке осталось только {stock_total:g} порц. Сначала пополни запас.")

    reward = ""
    portion_nutrition = scaled_nutrition(result, fraction=fraction)
    payload = {"recipe_id": recipe.id, "recipe_name": recipe.name, "fraction": fraction,
               "source": "recipe_book", **{key: portion_nutrition.get(key) for key in (
                   "calories_kcal", "protein_g", "fat_g", "carbs_g", "portion_grams")}}
    if category == "meal":
        stat.meals += 1
        rewarded = stat.meals <= 3
        await apply_reward(session, profile, event_type="meal_complete_recipe" if rewarded else "meal_recipe_unrewarded",
                           xp=20 if rewarded else 0, coins=3 if rewarded else 0, payload=payload)
        if rewarded:
            reward = "\n+20 XP · +3 монеты"
    elif category == "snack":
        stat.snacks += 1
        await apply_reward(session, profile, event_type="snack_recipe", payload=payload)
    elif category == "treat":
        stat.snacks += 1
        await apply_reward(session, profile, event_type="treat_logged", payload=payload)
    else:
        stat.drinks += 1
        # Low-calorie drinks continue to count as hydration, even when saved as recipes.
        kcal = _num(result.get("calories_kcal"))
        weight = _num(result.get("reference_grams"))
        plain = kcal is not None and ((weight and kcal / weight * 100 <= 5) or (not weight and kcal <= 5))
        if plain:
            stat.plain_drinks += 1
            hydration = await record_hydration(session, profile, stat, source="saved_recipe")
            if hydration:
                reward = "\n+3 XP · +1 связь с Тори"
        else:
            stat.caloric_drinks += 1
            await apply_reward(session, profile, event_type="drink_recipe", payload=payload)

    label = (f"{grams:g} г" if grams is not None else f"{fraction:g} порц.")
    row = await record_food_nutrition(session, user_id=profile.id, logged_on=day,
                                     analysis_id=None, category=category,
                                     result=result, fraction=fraction, portion_label=label)
    # Mark known origin/uncertainty so the coach doesn't mistake a photo guess for exact data.
    row.nutrition_source = "photo_estimate" if result.get("nutrition_source") == "photo_estimate" else str(result.get("nutrition_source") or "none")
    recipe.uses = (recipe.uses or 0) + 1
    recipe.last_used_at = datetime.now()
    remainder = fraction
    for batch in batches:
        taken = min(batch.remaining_portions, remainder)
        batch.remaining_portions = round(max(0.0, batch.remaining_portions - taken), 3)
        remainder = round(remainder - taken, 3)
        if remainder <= 0:
            break

    kcal = row.calories_kcal
    kcal_text = f" · ≈{kcal:g} ккал" if kcal is not None else ""
    return row, f"✅ {recipe.name}: {label}{kcal_text}{reward}"


def recipe_from_ai(*, user_id: int, details: dict[str, Any], name: str | None = None) -> SavedRecipe:
    category = str(details.get("category") or "meal")
    if category not in CATEGORIES:
        category = "meal"
    return SavedRecipe(
        user_id=user_id, name=str(name or details.get("name") or "Новый рецепт")[:240],
        category=category, ingredients=str(details.get("ingredients") or "")[:4000],
        nutrition_source="mixed" if details.get("confidence") == "high" else "photo_estimate",
        confidence=str(details.get("confidence") or "low"),
        portion_grams=_num(details.get("portion_grams")),
        calories_kcal=_num(details.get("calories_kcal")),
        protein_g=_num(details.get("protein_g")), fat_g=_num(details.get("fat_g")),
        carbs_g=_num(details.get("carbs_g")),
    )
