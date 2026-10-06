from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EveningNutritionReview, UserProfile
from app.services.ai_engine import ai_enabled, generate_evening_nutrition_review
from app.services.ai_features import build_ai_context, get_coach_rules, reserve_ai_call
from app.services.challenges import challenge_state, challenge_summary, evaluate_plan, get_plan
from app.services.nutrition import daily_nutrition_entries


def _entries_text(rows) -> str:
    if not rows:
        return "Подробных записей еды через фото нет."
    lines = []
    for row in rows:
        macros = []
        if row.calories_kcal is not None:
            macros.append(f"{int(round(row.calories_kcal))} ккал")
        if row.protein_g is not None:
            macros.append(f"Б {row.protein_g:g}")
        if row.fat_g is not None:
            macros.append(f"Ж {row.fat_g:g}")
        if row.carbs_g is not None:
            macros.append(f"У {row.carbs_g:g}")
        suffix = " · " + " · ".join(macros) if macros else ""
        source = {"label": "этикетка", "mixed": "смешанные данные", "photo_estimate": "оценка по фото", "none": "без КБЖУ"}.get(row.nutrition_source, row.nutrition_source)
        confidence = {"high": "высокая", "medium": "средняя", "low": "низкая"}.get(row.confidence, row.confidence)
        lines.append(
            f"- {row.category}: {row.description} ({row.portion_label}){suffix}; "
            f"источник: {source}, уверенность: {confidence}"
        )
    return "\n".join(lines)


async def get_review(session: AsyncSession, user_id: int, day: date) -> EveningNutritionReview | None:
    return await session.scalar(
        select(EveningNutritionReview).where(
            EveningNutritionReview.user_id == user_id,
            EveningNutritionReview.review_date == day,
        )
    )


async def create_evening_review(
    session: AsyncSession,
    profile: UserProfile,
    day: date,
    *,
    force: bool = False,
    finalize_challenge: bool = True,
) -> tuple[EveningNutritionReview, bool, bool]:
    """Create one AI review. Returns (review, challenge_chest_awarded, created_now)."""
    existing = await get_review(session, profile.id, day)
    if existing and not force:
        return existing, False, False

    context = await build_ai_context(session, profile)
    entries = await daily_nutrition_entries(session, profile.id, day)
    coach_rules = await get_coach_rules(session, profile.id)

    plan = await get_plan(session, profile.id, day)
    chest = False
    newly_completed_titles: list[str] = []
    if plan is None:
        challenge_text = "Челленджи сегодня не выбирались."
    else:
        before_completed = set(challenge_state(plan)["completed"])
        _challenge_status, challenge_values, chest_count = await evaluate_plan(
            session, profile, plan, final=finalize_challenge
        )
        chest = bool(chest_count)
        after_completed = set(challenge_state(plan)["completed"])
        if finalize_challenge:
            options = list(plan.options or [])
            for index in sorted(after_completed - before_completed):
                if 0 <= index < len(options):
                    newly_completed_titles.append(str(options[index].get("title") or "Испытание"))
        challenge_text = challenge_summary(plan, challenge_values, final=True)

    result = None
    if ai_enabled() and await reserve_ai_call(session, profile.id, "review"):
        await session.commit()
        try:
            result = await generate_evening_nutrition_review(
                context=context,
                entries_text=_entries_text(entries),
                coach_rules=coach_rules,
                challenge_text=challenge_text,
            )
        except Exception as exc:
            print("AI evening review error:", repr(exc))

    if not result:
        # Graceful fallback: no fake nutrient judgement if AI is unavailable.
        if entries or context["meals"] or context["snacks"]:
            text = (
                f"Сегодня записано: полноценная еда {context['meals']}, перекусы {context['snacks']}, "
                f"вкусняшки {context['treats_logged']}. Подробный AI-разбор сейчас не получился, "
                "поэтому не буду выдумывать выводы."
            )
        else:
            text = "Сегодня почти нет записей по еде, поэтому нормальный разбор делать не из чего."
        result = {"text": text, "emotion": "neutral", "tomorrow_focus": "Просто продолжай отмечать еду."}

    if newly_completed_titles:
        reward_xp = int(plan.reward_xp or 0) * len(newly_completed_titles) if plan else 0
        reward_coins = int(plan.reward_coins or 0) * len(newly_completed_titles) if plan else 0
        completed_lines = "\n".join(f"✅ {title}" for title in newly_completed_titles)
        result["text"] = (
            str(result.get("text") or "")
            + "\n\n🎯 Испытания закрыты по итогам дня:\n"
            + completed_lines
            + f"\n+{reward_xp} XP · +{reward_coins} монет"
            + ("\n🎁 +1 Сундук испытания" if chest else "")
        )

    if existing:
        existing.text = str(result.get("text") or "")[:8000]
        existing.emotion = str(result.get("emotion") or "neutral")[:30]
        existing.tomorrow_focus = str(result.get("tomorrow_focus") or "")[:320]
        review = existing
    else:
        review = EveningNutritionReview(
            user_id=profile.id,
            review_date=day,
            text=str(result.get("text") or "")[:8000],
            emotion=str(result.get("emotion") or "neutral")[:30],
            tomorrow_focus=str(result.get("tomorrow_focus") or "")[:320],
        )
        session.add(review)
    await session.flush()
    return review, chest, True
