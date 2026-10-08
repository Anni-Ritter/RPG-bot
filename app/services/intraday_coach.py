from __future__ import annotations

from app.db import SessionLocal
from app.services.ai_engine import ai_enabled, generate_intraday_nutrition_advice
from app.services.ai_features import build_ai_context, get_coach_rules, now_local, reserve_ai_call
from app.services.nutrition import daily_nutrition_entries
from app.services.rewards import get_or_create_profile


def _entries_text(rows) -> str:
    if not rows:
        return "Подробных записей еды через фото пока нет."

    lines: list[str] = []
    category_labels = {
        "meal": "полноценная еда",
        "snack": "перекус",
        "treat": "вкусняшка",
        "drink": "напиток",
    }
    source_labels = {
        "label": "этикетка",
        "mixed": "этикетка/комментарий/фото",
        "photo_estimate": "оценка по фото",
        "none": "без точных КБЖУ",
    }
    for row in rows:
        macros: list[str] = []
        if row.calories_kcal is not None:
            macros.append(f"{int(round(row.calories_kcal))} ккал")
        if row.protein_g is not None:
            macros.append(f"Б {row.protein_g:g}")
        if row.fat_g is not None:
            macros.append(f"Ж {row.fat_g:g}")
        if row.carbs_g is not None:
            macros.append(f"У {row.carbs_g:g}")

        macro_text = " · ".join(macros) if macros else "КБЖУ не определены"
        category = category_labels.get(row.category, row.category)
        source = source_labels.get(row.nutrition_source, row.nutrition_source)
        lines.append(
            f"- {category}: {row.description or 'без описания'}; {row.portion_label}; "
            f"{macro_text}; источник: {source}; уверенность: {row.confidence}"
        )
    return "\n".join(lines)


async def create_intraday_food_feedback(
    user_id: int,
    display_name: str | None,
    *,
    latest_action: str,
) -> dict | None:
    """Generate a short Selin course-correction after a food-related action.

    This deliberately does not persist a review: it is a live reaction to the
    current day. Evening review remains the canonical end-of-day summary.
    """
    if not ai_enabled():
        return None

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, user_id, display_name)
        context = await build_ai_context(session, profile)
        rows = await daily_nutrition_entries(session, profile.id, now_local().date())
        coach_rules = await get_coach_rules(session, profile.id)
        allowed = await reserve_ai_call(session, profile.id, "chat")
        await session.commit()

    if not allowed:
        return None

    try:
        result = await generate_intraday_nutrition_advice(
            context=context,
            entries_text=_entries_text(rows),
            coach_rules=coach_rules,
            latest_action=latest_action,
        )
    except Exception as exc:
        print("AI intraday nutrition advice error:", repr(exc))
        return None

    if not result or not result.get("should_send") or not str(result.get("text") or "").strip():
        return None
    return {
        "text": str(result.get("text") or "").strip(),
        "emotion": str(result.get("emotion") or "neutral"),
    }
