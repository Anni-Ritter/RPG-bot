"""Build a bounded, truthful history of meal entries for a multi-day AI review."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyStat, FoodNutritionLog
from app.services.ai_features import daily_treat_count

PERIODS = (3, 7, 14, 30)


def _number(value: float | None, unit: str) -> str:
    return f"{float(value):.1f}{unit}" if value is not None else "неизвестно"


def _short(text: str, limit: int = 110) -> str:
    text = " ".join(str(text or "").split())
    return text[: limit - 1] + "…" if len(text) > limit else text


async def build_nutrition_history(
    session: AsyncSession, user_id: int, *, days: int, today: date
) -> dict:
    """Use *completed* days only; missed logs mean missing data, not zero food."""
    if days not in PERIODS:
        raise ValueError("Unsupported history period")
    start = today - timedelta(days=days)
    end = today - timedelta(days=1)

    stats = list((await session.scalars(select(DailyStat).where(
        DailyStat.user_id == user_id,
        DailyStat.stat_date >= start,
        DailyStat.stat_date <= end,
    ))).all())
    entries = list((await session.scalars(select(FoodNutritionLog).where(
        FoodNutritionLog.user_id == user_id,
        FoodNutritionLog.logged_on >= start,
        FoodNutritionLog.logged_on <= end,
    ).order_by(FoodNutritionLog.logged_on, FoodNutritionLog.id))).all())

    by_day = defaultdict(list)
    for entry in entries:
        by_day[entry.logged_on].append(entry)
    stats_by_day = {s.stat_date: s for s in stats}
    repeated = Counter(_short(e.description, 80).lower() for e in entries if e.description)
    meaningful_days = 0
    days_with_macros = 0
    day_texts = []
    limits = 9 if days <= 14 else 5

    for offset in range(days):
        day = start + timedelta(days=offset)
        stat = stats_by_day.get(day)
        day_entries = by_day.get(day, [])
        meals = int(stat.meals or 0) if stat else 0
        snacks = int(stat.snacks or 0) if stat else 0
        drinks = int(stat.drinks or 0) if stat else 0
        treats = await daily_treat_count(session, user_id, day) if (stat or day_entries) else 0
        logged = bool(meals or snacks or drinks or day_entries)
        if not logged:
            day_texts.append(f"{day.isoformat()}: записей питания нет (НЕ означает, что человек не ел).")
            continue
        meaningful_days += 1
        known = [row for row in day_entries if row.calories_kcal is not None]
        if known:
            days_with_macros += 1
        total_kcal = sum(row.calories_kcal for row in known)
        photo_approx = sum(row.nutrition_source == "photo_estimate" for row in day_entries)
        low_confidence = sum(row.confidence == "low" for row in day_entries)
        calories = f"{total_kcal:.0f} ккал только из {len(known)} записей" if known else "КБЖУ неизвестны"
        lines = [
            f"{day.isoformat()}: полноценная еда {meals}; перекусы (включая вкусняшки) {snacks}; "
            f"вкусняшки {treats}; напитки {drinks}; подробно сохранено {len(day_entries)}; "
            f"известно {calories}; фотооценок {photo_approx}; низкая уверенность {low_confidence}."
        ]
        for item in day_entries[:limits]:
            macros = (
                f"{_number(item.calories_kcal, ' ккал')}, "
                f"Б {_number(item.protein_g, ' г')} / Ж {_number(item.fat_g, ' г')} / "
                f"У {_number(item.carbs_g, ' г')}"
            )
            lines.append(
                f"  - {_short(item.description)} [{item.category}, {_short(item.portion_label, 40)}]: "
                f"{macros}; источник={item.nutrition_source}, точность={item.confidence}"
            )
        if len(day_entries) > limits:
            lines.append(f"  - ещё {len(day_entries) - limits} записей, не перечислены из-за длины отчёта")
        if meals + snacks + drinks > len(day_entries):
            lines.append("  - часть еды/напитков отмечена без подробного КБЖУ")
        day_texts.append("\n".join(lines))

    top_dishes = ", ".join(f"{name} ({count})" for name, count in repeated.most_common(8)) or "нет"
    return {
        "start": start,
        "end": end,
        "days": days,
        "recorded_days": meaningful_days,
        "nutrition_days": days_with_macros,
        "detail_count": len(entries),
        "history_text": "\n\n".join(day_texts),
        "repeated_text": top_dishes,
    }

# SELIN_LIVING_RHYTHM_20261027_BEGIN
_living_rhythm_original_history = build_nutrition_history

async def build_nutrition_history(session, user_id, *, days, today):
    result = await _living_rhythm_original_history(session, user_id, days=days, today=today)
    from datetime import timedelta
    from app.services.living_rhythm import grouped_history_text
    groups = await grouped_history_text(session, user_id, today - timedelta(days=days), today - timedelta(days=1))
    if groups:
        result = dict(result)
        result['history_text'] = str(result.get('history_text') or '') + (
            '\n\nГруппы подтверждённых приёмов пищи (одна группа может включать несколько позиций):\n' + groups
        )
    return result
# SELIN_LIVING_RHYTHM_20261027_END
