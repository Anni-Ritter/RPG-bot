from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    AIChatState,
    AIQuestOffer,
    AIUsageDaily,
    CustomQuest,
    DailyStat,
    EveningNutritionReview,
    GameEvent,
    UserCoachRules,
    UserMemory,
    UserNutritionGoal,
    UserProfile,
)
from app.services.coach_defaults import DEFAULT_COACH_RULES
from app.services.levels import level_from_xp
from app.services.nutrition import daily_nutrition_totals
from app.services.story import (
    advance_week1_if_due,
    affinity_label,
    current_story_objective,
    get_or_create_story_progress,
    initialize_week1_v2,
    story_day,
)

TZ = ZoneInfo(settings.timezone)

# Legacy values are kept so old rows / imports remain harmless after V9.
# Free chat is no longer unlocked by spending XP or limited to six replies.
CHAT_SESSION_XP = 75
CHAT_SESSION_TURNS = 6
MAX_BANKED_CHAT_SESSIONS = 3

AI_QUEST_REWARDS = {
    "easy": (10, 2),
    "normal": (20, 4),
    "hard": (35, 7),
}

MEMORY_CATEGORIES = {"preference", "routine", "social", "pet", "goal", "general"}
MAX_USER_MEMORIES = 30


def now_local() -> datetime:
    return datetime.now(TZ)


async def get_or_create_ai_state(session: AsyncSession, profile: UserProfile) -> AIChatState:
    state = await session.get(AIChatState, profile.id)
    if state:
        return state
    state = AIChatState(
        user_id=profile.id,
        xp_accounted=profile.xp,
        active_turns_left=0,
        recent_messages=[],
    )
    session.add(state)
    await session.flush()
    return state


# Legacy helpers kept for older handlers / data. V9 does not use them to gate chat.
def normalize_chat_bank(profile: UserProfile, state: AIChatState) -> None:
    return None


def available_chat_sessions(profile: UserProfile, state: AIChatState) -> int:
    return 0


def xp_until_next_chat(profile: UserProfile, state: AIChatState) -> int:
    return 0


def begin_chat_session(profile: UserProfile, state: AIChatState) -> bool:
    state.active_turns_left = 0
    return True


def append_recent_message(state: AIChatState, role: str, text: str) -> None:
    history = list(state.recent_messages or [])
    history.append({"role": role, "text": text[:1600]})
    state.recent_messages = history[-24:]


def recent_chat_text(state: AIChatState) -> str:
    rows = []
    for item in list(state.recent_messages or [])[-18:]:
        role = "Игрок" if item.get("role") == "user" else "Селин"
        rows.append(f"{role}: {item.get('text', '')}")
    return "\n".join(rows) if rows else "Предыдущего свободного разговора пока нет."


async def get_or_create_usage(session: AsyncSession, user_id: int, day: date) -> AIUsageDaily:
    usage = await session.scalar(
        select(AIUsageDaily).where(
            AIUsageDaily.user_id == user_id,
            AIUsageDaily.usage_date == day,
        )
    )
    if usage:
        return usage
    usage = AIUsageDaily(user_id=user_id, usage_date=day)
    session.add(usage)
    await session.flush()
    return usage


async def reserve_ai_call(session: AsyncSession, user_id: int, kind: str) -> bool:
    """Record an AI call and keep only a safety cap for non-chat/background work.

    Chat is intentionally unlimited in V9: story state can make Selin unavailable,
    but normal conversation is not paid for with XP and does not run out of turns.
    Existing OPENAI_DAILY_CALL_LIMIT therefore protects vision/background calls only.
    """
    usage = await get_or_create_usage(session, user_id, now_local().date())
    limit = int(settings.openai_daily_call_limit or 0)
    non_chat_calls = int(usage.vision_calls or 0) + int(usage.initiative_calls or 0)
    if kind != "chat" and limit > 0 and non_chat_calls >= limit:
        return False

    usage.calls += 1
    if kind == "chat":
        usage.chat_calls += 1
    elif kind == "vision":
        usage.vision_calls += 1
    else:
        # Challenges, evening reviews and autonomous messages are all background AI.
        usage.initiative_calls += 1
    return True


async def can_offer_ai_quest(session: AsyncSession, user_id: int, day: date) -> bool:
    existing = await session.scalar(
        select(AIQuestOffer.id).where(
            AIQuestOffer.user_id == user_id,
            AIQuestOffer.offered_on == day,
        ).limit(1)
    )
    return existing is None


async def create_ai_quest_offer(
    session: AsyncSession,
    profile: UserProfile,
    *,
    title: str,
    difficulty: str,
    reason: str = "",
) -> AIQuestOffer:
    if difficulty not in AI_QUEST_REWARDS:
        difficulty = "easy"
    xp, coins = AI_QUEST_REWARDS[difficulty]
    offer = AIQuestOffer(
        user_id=profile.id,
        offered_on=now_local().date(),
        title=title[:240],
        difficulty=difficulty,
        reason=reason[:320],
        reward_xp=xp,
        reward_coins=coins,
        status="offered",
    )
    session.add(offer)
    await session.flush()
    return offer


async def accept_ai_quest(session: AsyncSession, profile: UserProfile, offer: AIQuestOffer) -> CustomQuest:
    quest = CustomQuest(
        user_id=profile.id,
        title=offer.title,
        difficulty=offer.difficulty,
        reward_xp=offer.reward_xp,
        reward_coins=offer.reward_coins,
        status="active",
    )
    session.add(quest)
    await session.flush()
    offer.status = "accepted"
    offer.custom_quest_id = quest.id
    return quest


def _memory_key(category: str, key: str, value: str) -> str:
    raw = (key or value[:80]).strip().lower()
    raw = re.sub(r"\s+", "_", raw)
    raw = re.sub(r"[^a-zа-яё0-9_\-]", "", raw, flags=re.IGNORECASE)
    return f"{category}:{raw[:90] or 'fact'}"


async def store_ai_memories(
    session: AsyncSession,
    user_id: int,
    memories: list[dict] | None,
    *,
    source: str = "chat",
) -> int:
    """Upsert a few non-sensitive facts explicitly extracted from the user's own text."""
    saved = 0
    for item in list(memories or [])[:3]:
        category = str(item.get("category") or "general").strip().lower()
        if category not in MEMORY_CATEGORIES:
            category = "general"
        value = str(item.get("value") or "").strip()
        key = str(item.get("key") or "").strip()
        if not value or len(value) < 3:
            continue
        memory_key = _memory_key(category, key, value)
        row = await session.scalar(
            select(UserMemory).where(
                UserMemory.user_id == user_id,
                UserMemory.memory_key == memory_key,
            )
        )
        if row:
            row.value = value[:500]
            row.category = category
            row.source = source[:40]
        else:
            session.add(
                UserMemory(
                    user_id=user_id,
                    memory_key=memory_key,
                    category=category,
                    value=value[:500],
                    source=source[:40],
                )
            )
        saved += 1

    # Keep memory intentionally compact so every chat request stays cheap.
    rows = list(
        (
            await session.scalars(
                select(UserMemory)
                .where(UserMemory.user_id == user_id)
                .order_by(UserMemory.updated_at.desc(), UserMemory.id.desc())
            )
        ).all()
    )
    for stale in rows[MAX_USER_MEMORIES:]:
        await session.delete(stale)
    return saved


async def user_memory_text(session: AsyncSession, user_id: int, limit: int = 24) -> str:
    rows = list(
        (
            await session.scalars(
                select(UserMemory)
                .where(UserMemory.user_id == user_id)
                .order_by(UserMemory.updated_at.desc(), UserMemory.id.desc())
                .limit(limit)
            )
        ).all()
    )
    if not rows:
        return "Пока нет сохранённых деталей."
    return "\n".join(f"- {row.value}" for row in reversed(rows))


async def clear_user_memories(session: AsyncSession, user_id: int) -> None:
    await session.execute(delete(UserMemory).where(UserMemory.user_id == user_id))


async def get_custom_coach_rules(session: AsyncSession, user_id: int) -> str:
    row = await session.get(UserCoachRules, user_id)
    return (row.rules or "").strip() if row else ""


async def get_coach_rules(session: AsyncSession, user_id: int) -> str:
    custom = await get_custom_coach_rules(session, user_id)
    if custom:
        return DEFAULT_COACH_RULES + "\n\nДополнительные рекомендации тренера:\n" + custom
    return DEFAULT_COACH_RULES


async def set_coach_rules(session: AsyncSession, user_id: int, rules: str) -> None:
    row = await session.get(UserCoachRules, user_id)
    if row:
        row.rules = rules[:8000]
    else:
        session.add(UserCoachRules(user_id=user_id, rules=rules[:8000]))


async def get_calorie_target(session: AsyncSession, user_id: int) -> int:
    row = await session.get(UserNutritionGoal, user_id)
    return int(row.calorie_target_kcal) if row else 1500


async def set_calorie_target(session: AsyncSession, user_id: int, target_kcal: int) -> int:
    target = max(1200, min(int(target_kcal), 4000))
    row = await session.get(UserNutritionGoal, user_id)
    if row:
        row.calorie_target_kcal = target
    else:
        session.add(UserNutritionGoal(user_id=user_id, calorie_target_kcal=target))
    return target


async def daily_treat_count(session: AsyncSession, user_id: int, day: date) -> int:
    start = datetime.combine(day, datetime.min.time(), tzinfo=TZ)
    end = datetime.combine(day, datetime.max.time(), tzinfo=TZ)
    count = await session.scalar(
        select(func.count(GameEvent.id)).where(
            GameEvent.user_id == user_id,
            GameEvent.event_type.in_([
                "treat_logged",
                "temptation_accepted",
                "temptation_after_pause_accepted",
            ]),
            GameEvent.occurred_at >= start,
            GameEvent.occurred_at <= end,
        )
    )
    return int(count or 0)


async def build_ai_context(session: AsyncSession, profile: UserProfile) -> dict:
    today = now_local().date()
    progress = await get_or_create_story_progress(session, profile.id, today)
    initialize_week1_v2(progress, profile, today)
    advance_week1_if_due(progress, today)
    daily = await session.scalar(
        select(DailyStat).where(
            DailyStat.user_id == profile.id,
            DailyStat.stat_date == today,
        )
    )
    nutrition = await daily_nutrition_totals(session, profile.id, today)
    previous_review = await session.scalar(
        select(EveningNutritionReview)
        .where(
            EveningNutritionReview.user_id == profile.id,
            EveningNutritionReview.review_date < today,
        )
        .order_by(EveningNutritionReview.review_date.desc())
        .limit(1)
    )
    level, _, next_threshold = level_from_xp(profile.xp)
    day = story_day(progress, today)
    calorie_target = await get_calorie_target(session, profile.id)
    calorie_delta = int(nutrition["calories_kcal"]) - calorie_target
    return {
        "name": profile.display_name or "Проводник",
        "story_day": day,
        "level": level,
        "xp": profile.xp,
        "next_level_xp": next_threshold,
        "strength": profile.strength,
        "willpower": profile.willpower,
        "tori_bond": profile.tori_bond,
        "selin_relation": affinity_label(progress),
        "objective": current_story_objective(profile, progress, today),
        "steps": daily.steps if daily else 0,
        "meals": daily.meals if daily else 0,
        "snacks": daily.snacks if daily else 0,
        "water": daily.water if daily else 0,
        "drinks": daily.drinks if daily else 0,
        "energy_drinks": daily.energy_drinks if daily else 0,
        "temptation_count": daily.temptation_count if daily else 0,
        "treats_logged": await daily_treat_count(session, profile.id, today),
        "nutrition_count": nutrition["count"],
        "nutrition_entry_count": nutrition.get("entry_count", nutrition["count"]),
        "nutrition_photo_estimate_count": nutrition.get("photo_estimate_count", 0),
        "nutrition_low_confidence_count": nutrition.get("low_confidence_count", 0),
        "nutrition_calories": nutrition["calories_kcal"],
        "calorie_target_kcal": calorie_target,
        "calorie_delta_kcal": calorie_delta,
        "nutrition_protein": nutrition["protein_g"],
        "nutrition_fat": nutrition["fat_g"],
        "nutrition_carbs": nutrition["carbs_g"],
        "memories": await user_memory_text(session, profile.id),
        "coach_rules": await get_coach_rules(session, profile.id),
        "previous_focus": (previous_review.tomorrow_focus or "").strip() if previous_review else "",
    }
