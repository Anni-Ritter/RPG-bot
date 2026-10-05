from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FoodNutritionLog


NUTRIENT_KEYS = ("calories_kcal", "protein_g", "fat_g", "carbs_g")
PER_100_KEYS = {
    "calories_kcal": "per_100g_calories_kcal",
    "protein_g": "per_100g_protein_g",
    "fat_g": "per_100g_fat_g",
    "carbs_g": "per_100g_carbs_g",
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = float(value)
        if value >= 0:
            return value
    return None


def _round_macro(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _round_kcal(value: float | None) -> int | None:
    return None if value is None else int(round(value))


def _macro_kcal(protein: float | None, fat: float | None, carbs: float | None) -> float | None:
    if protein is None or fat is None or carbs is None:
        return None
    return protein * 4 + carbs * 4 + fat * 9


def normalize_food_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Fill safe derived nutrition values without inventing missing food facts."""
    result = dict(raw or {})

    total_p = _number(result.get("protein_g"))
    total_f = _number(result.get("fat_g"))
    total_c = _number(result.get("carbs_g"))
    total_kcal = _number(result.get("calories_kcal"))
    if total_kcal is None:
        total_kcal = _macro_kcal(total_p, total_f, total_c)
        if total_kcal is not None:
            result["calories_kcal"] = _round_kcal(total_kcal)

    p100 = _number(result.get("per_100g_protein_g"))
    f100 = _number(result.get("per_100g_fat_g"))
    c100 = _number(result.get("per_100g_carbs_g"))
    kcal100 = _number(result.get("per_100g_calories_kcal"))
    if kcal100 is None:
        kcal100 = _macro_kcal(p100, f100, c100)
        if kcal100 is not None:
            result["per_100g_calories_kcal"] = _round_kcal(kcal100)

    reference_grams = _number(result.get("reference_grams"))
    if reference_grams and reference_grams > 0:
        scale = reference_grams / 100.0
        if _number(result.get("calories_kcal")) is None and kcal100 is not None:
            result["calories_kcal"] = _round_kcal(kcal100 * scale)
        if _number(result.get("protein_g")) is None and p100 is not None:
            result["protein_g"] = _round_macro(p100 * scale)
        if _number(result.get("fat_g")) is None and f100 is not None:
            result["fat_g"] = _round_macro(f100 * scale)
        if _number(result.get("carbs_g")) is None and c100 is not None:
            result["carbs_g"] = _round_macro(c100 * scale)

    # If the model read totals + an explicit weight but omitted per-100g values,
    # derive them so manual gram entry can still be recalculated locally.
    reference_grams = _number(result.get("reference_grams"))
    if reference_grams and reference_grams > 0 and result.get("nutrition_source") in {"label", "mixed"}:
        for total_key, per_key in PER_100_KEYS.items():
            total_value = _number(result.get(total_key))
            per_value = _number(result.get(per_key))
            if per_value is None and total_value is not None:
                derived = total_value * 100.0 / reference_grams
                result[per_key] = _round_kcal(derived) if total_key == "calories_kcal" else _round_macro(derived)

    low = _number(result.get("calories_min_kcal"))
    high = _number(result.get("calories_max_kcal"))
    best = _number(result.get("calories_kcal"))
    if best is not None and low is None and high is None and result.get("nutrition_source") == "photo_estimate":
        # This is only an uncertainty band around the model's own estimate.
        result["calories_min_kcal"] = max(0, int(round(best * 0.85)))
        result["calories_max_kcal"] = int(round(best * 1.15))

    return result


def can_scale_by_grams(result: dict[str, Any]) -> bool:
    normalized = normalize_food_result(result)
    return any(_number(normalized.get(key)) is not None for key in PER_100_KEYS.values())


def scaled_nutrition(
    result: dict[str, Any],
    *,
    fraction: float = 1.0,
    grams: float | None = None,
) -> dict[str, float | int | None]:
    normalized = normalize_food_result(result)
    fraction = max(0.0, min(float(fraction), 10.0))

    if grams is not None:
        grams = max(0.0, min(float(grams), 10000.0))
        scale = grams / 100.0
        values: dict[str, float | int | None] = {"portion_grams": round(grams, 1)}
        for total_key, per_key in PER_100_KEYS.items():
            per_value = _number(normalized.get(per_key))
            value = per_value * scale if per_value is not None else None
            values[total_key] = _round_kcal(value) if total_key == "calories_kcal" else _round_macro(value)
        return values

    values = {"portion_grams": None}
    reference_grams = _number(normalized.get("reference_grams"))
    if reference_grams is not None:
        values["portion_grams"] = round(reference_grams * fraction, 1)

    for key in NUTRIENT_KEYS:
        value = _number(normalized.get(key))
        scaled = value * fraction if value is not None else None
        values[key] = _round_kcal(scaled) if key == "calories_kcal" else _round_macro(scaled)
    return values


async def record_food_nutrition(
    session: AsyncSession,
    *,
    user_id: int,
    logged_on: date,
    analysis_id: int,
    category: str,
    result: dict[str, Any],
    fraction: float = 1.0,
    grams: float | None = None,
    portion_label: str = "вся порция",
) -> FoodNutritionLog:
    normalized = normalize_food_result(result)
    nutrition = scaled_nutrition(normalized, fraction=fraction, grams=grams)
    row = FoodNutritionLog(
        user_id=user_id,
        logged_on=logged_on,
        analysis_id=analysis_id,
        category=category,
        description=str(normalized.get("summary") or "Еда по фото")[:300],
        nutrition_source=str(normalized.get("nutrition_source") or "none")[:30],
        confidence=str(normalized.get("confidence") or "low")[:20],
        portion_label=portion_label[:80],
        portion_grams=nutrition.get("portion_grams"),
        calories_kcal=nutrition.get("calories_kcal"),
        protein_g=nutrition.get("protein_g"),
        fat_g=nutrition.get("fat_g"),
        carbs_g=nutrition.get("carbs_g"),
    )
    session.add(row)
    await session.flush()
    return row


async def daily_nutrition_totals(session: AsyncSession, user_id: int, day: date) -> dict[str, float | int]:
    row = (
        await session.execute(
            select(
                func.count(FoodNutritionLog.calories_kcal),
                func.sum(FoodNutritionLog.calories_kcal),
                func.sum(FoodNutritionLog.protein_g),
                func.sum(FoodNutritionLog.fat_g),
                func.sum(FoodNutritionLog.carbs_g),
            ).where(
                FoodNutritionLog.user_id == user_id,
                FoodNutritionLog.logged_on == day,
            )
        )
    ).one()
    count, kcal, protein, fat, carbs = row
    return {
        "count": int(count or 0),
        "calories_kcal": int(round(float(kcal or 0))),
        "protein_g": round(float(protein or 0), 1),
        "fat_g": round(float(fat or 0), 1),
        "carbs_g": round(float(carbs or 0), 1),
    }
