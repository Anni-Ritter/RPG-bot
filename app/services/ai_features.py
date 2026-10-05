from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    AIChatState,
    AIQuestOffer,
    AIUsageDaily,
    CustomQuest,
    DailyStat,
    UserProfile,
)
from app.services.levels import level_from_xp
from app.services.nutrition import daily_nutrition_totals
from app.services.story import (
    affinity_label,
    current_story_objective,
    get_or_create_story_progress,
    initialize_week1_v2,
    advance_week1_if_due,
    story_day,
)

TZ = ZoneInfo(settings.timezone)

CHAT_SESSION_XP = 75
CHAT_SESSION_TURNS = 6
MAX_BANKED_CHAT_SESSIONS = 3

AI_QUEST_REWARDS = {
    "easy": (10, 2),
    "normal": (20, 4),
    "hard": (35, 7),
}


def now_local() -> datetime:
    return datetime.now(TZ)


async def get_or_create_ai_state(
    session: AsyncSession,
    profile: UserProfile,
) -> AIChatState:
    state = await session.get(AIChatState, profile.id)
    if state:
        normalize_chat_bank(profile, state)
        return state

    # A player who already had progress before AI was added can bank at most
    # three conversations, not their entire historic XP balance.
    initial_accounted = max(0, profile.xp - CHAT_SESSION_XP * MAX_BANKED_CHAT_SESSIONS)
    state = AIChatState(
        user_id=profile.id,
        xp_accounted=initial_accounted,
        active_turns_left=0,
        recent_messages=[],
    )
    session.add(state)
    await session.flush()
    return state


def normalize_chat_bank(profile: UserProfile, state: AIChatState) -> None:
    max_bank_xp = CHAT_SESSION_XP * MAX_BANKED_CHAT_SESSIONS
    unaccounted = max(0, profile.xp - state.xp_accounted)
    if unaccounted > max_bank_xp:
        state.xp_accounted = max(0, profile.xp - max_bank_xp)


def available_chat_sessions(profile: UserProfile, state: AIChatState) -> int:
    normalize_chat_bank(profile, state)
    unaccounted = max(0, profile.xp - state.xp_accounted)
    return min(MAX_BANKED_CHAT_SESSIONS, unaccounted // CHAT_SESSION_XP)


def xp_until_next_chat(profile: UserProfile, state: AIChatState) -> int:
    normalize_chat_bank(profile, state)
    if available_chat_sessions(profile, state) > 0:
        return 0
    unaccounted = max(0, profile.xp - state.xp_accounted)
    return max(0, CHAT_SESSION_XP - unaccounted)


def begin_chat_session(profile: UserProfile, state: AIChatState) -> bool:
    if state.active_turns_left > 0:
        return True
    if available_chat_sessions(profile, state) <= 0:
        return False
    state.xp_accounted += CHAT_SESSION_XP
    state.active_turns_left = CHAT_SESSION_TURNS
    return True


def append_recent_message(state: AIChatState, role: str, text: str) -> None:
    history = list(state.recent_messages or [])
    history.append({"role": role, "text": text[:1200]})
    state.recent_messages = history[-16:]


def recent_chat_text(state: AIChatState) -> str:
    rows = []
    for item in list(state.recent_messages or [])[-12:]:
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
    usage = await get_or_create_usage(session, user_id, now_local().date())
    if usage.calls >= settings.openai_daily_call_limit:
        return False
    usage.calls += 1
    if kind == "chat":
        usage.chat_calls += 1
    elif kind == "vision":
        usage.vision_calls += 1
    elif kind == "initiative":
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
    level, _, next_threshold = level_from_xp(profile.xp)
    day = story_day(progress, today)
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
        "nutrition_count": nutrition["count"],
        "nutrition_calories": nutrition["calories_kcal"],
        "nutrition_protein": nutrition["protein_g"],
        "nutrition_fat": nutrition["fat_g"],
        "nutrition_carbs": nutrition["carbs_g"],
    }
