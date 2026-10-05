from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    JSON,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    character_name: Mapped[str] = mapped_column(String(80), default="Селин")
    familiar_name: Mapped[str] = mapped_column(String(80), default="Тори")

    xp: Mapped[int] = mapped_column(Integer, default=0)
    coins: Mapped[int] = mapped_column(Integer, default=0)
    strength: Mapped[int] = mapped_column(Integer, default=0)
    willpower: Mapped[int] = mapped_column(Integer, default=0)
    tori_bond: Mapped[int] = mapped_column(Integer, default=0)
    atelier_dust: Mapped[int] = mapped_column(Integer, default=0)
    trial_chests: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DailyStat(Base):
    __tablename__ = "daily_stats"
    __table_args__ = (UniqueConstraint("user_id", "stat_date", name="uq_daily_stats_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    stat_date: Mapped[date] = mapped_column(Date, index=True)

    meals: Mapped[int] = mapped_column(Integer, default=0)
    snacks: Mapped[int] = mapped_column(Integer, default=0)
    drinks: Mapped[int] = mapped_column(Integer, default=0)
    plain_drinks: Mapped[int] = mapped_column(Integer, default=0)
    caloric_drinks: Mapped[int] = mapped_column(Integer, default=0)
    energy_drinks: Mapped[int] = mapped_column(Integer, default=0)
    water: Mapped[int] = mapped_column(Integer, default=0)

    steps: Mapped[int] = mapped_column(Integer, default=0)
    step_tier: Mapped[str | None] = mapped_column(String(40), nullable=True)
    step_reward_xp: Mapped[int] = mapped_column(Integer, default=0)
    step_reward_coins: Mapped[int] = mapped_column(Integer, default=0)

    temptation_count: Mapped[int] = mapped_column(Integer, default=0)
    rewarded_temptations: Mapped[int] = mapped_column(Integer, default=0)


class GameEvent(Base):
    __tablename__ = "game_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    source: Mapped[str] = mapped_column(String(30), default="user")
    xp_delta: Mapped[int] = mapped_column(Integer, default=0)
    coins_delta: Mapped[int] = mapped_column(Integer, default=0)
    atelier_dust_delta: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class CustomQuest(Base):
    __tablename__ = "custom_quests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(240))
    difficulty: Mapped[str] = mapped_column(String(20), default="normal")
    reward_xp: Mapped[int] = mapped_column(Integer)
    reward_coins: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkoutSession(Base):
    __tablename__ = "workout_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "workout_date", "kind", name="uq_workout_user_date_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    workout_date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="available")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(60), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ScrollDefinition(Base):
    __tablename__ = "scroll_definitions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    rarity: Mapped[str] = mapped_column(String(20), index=True)
    item_type: Mapped[str] = mapped_column(String(30), index=True)
    collection: Mapped[str] = mapped_column(String(120))
    chapter: Mapped[int] = mapped_column(Integer)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(30), index=True)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)


class ScrollInventory(Base):
    __tablename__ = "scroll_inventory"
    __table_args__ = (UniqueConstraint("user_id", "scroll_id", name="uq_inventory_user_scroll"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    scroll_id: Mapped[str] = mapped_column(ForeignKey("scroll_definitions.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="sealed")
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    obtained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScrollGeneration(Base):
    __tablename__ = "scroll_generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    scroll_id: Mapped[str] = mapped_column(ForeignKey("scroll_definitions.id", ondelete="CASCADE"), index=True)
    telegram_file_id: Mapped[str] = mapped_column(String(512))
    telegram_file_unique_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChestOpen(Base):
    __tablename__ = "chest_opens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    chest_type: Mapped[str] = mapped_column(String(40), default="trial")
    rarity: Mapped[str] = mapped_column(String(20))
    scroll_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    atelier_dust_awarded: Mapped[int] = mapped_column(Integer, default=0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GachaState(Base):
    __tablename__ = "gacha_state"

    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), primary_key=True)
    season_number: Mapped[int] = mapped_column(Integer, default=1)
    common_streak: Mapped[int] = mapped_column(Integer, default=0)
    season_chests_opened: Mapped[int] = mapped_column(Integer, default=0)
    season_epic_plus_hit: Mapped[bool] = mapped_column(Boolean, default=False)


class StoryProgress(Base):
    __tablename__ = "story_progress"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    season_number: Mapped[int] = mapped_column(Integer, default=1)
    season_started_on: Mapped[date] = mapped_column(Date)
    last_viewed_day: Mapped[int] = mapped_column(Integer, default=0)
    flags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PendingTemptation(Base):
    __tablename__ = "pending_temptations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="waiting", index=True)
    reward_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserAchievement(Base):
    __tablename__ = "user_achievements"
    __table_args__ = (UniqueConstraint("user_id", "code", name="uq_user_achievement_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(160))
    reward_text: Mapped[str] = mapped_column(String(240), default="")
    unlocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AIChatState(Base):
    __tablename__ = "ai_chat_state"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    xp_accounted: Mapped[int] = mapped_column(Integer, default=0)
    active_turns_left: Mapped[int] = mapped_column(Integer, default=0)
    recent_messages: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    last_initiative_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AIQuestOffer(Base):
    __tablename__ = "ai_quest_offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    offered_on: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(240))
    difficulty: Mapped[str] = mapped_column(String(20), default="easy")
    reason: Mapped[str] = mapped_column(String(320), default="")
    reward_xp: Mapped[int] = mapped_column(Integer, default=10)
    reward_coins: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[str] = mapped_column(String(20), default="offered", index=True)
    custom_quest_id: Mapped[int | None] = mapped_column(ForeignKey("custom_quests.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AIImageAnalysis(Base):
    __tablename__ = "ai_image_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    analysis_type: Mapped[str] = mapped_column(String(20), index=True)
    telegram_file_id: Mapped[str] = mapped_column(String(512))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FoodNutritionLog(Base):
    __tablename__ = "food_nutrition_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    logged_on: Mapped[date] = mapped_column(Date, index=True)
    analysis_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_image_analyses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(20), default="meal")
    description: Mapped[str] = mapped_column(String(300), default="")
    nutrition_source: Mapped[str] = mapped_column(String(30), default="none")
    confidence: Mapped[str] = mapped_column(String(20), default="low")
    portion_label: Mapped[str] = mapped_column(String(80), default="вся порция")
    portion_grams: Mapped[float | None] = mapped_column(Float, nullable=True)
    calories_kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AIUsageDaily(Base):
    __tablename__ = "ai_usage_daily"
    __table_args__ = (UniqueConstraint("user_id", "usage_date", name="uq_ai_usage_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    usage_date: Mapped[date] = mapped_column(Date, index=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    chat_calls: Mapped[int] = mapped_column(Integer, default=0)
    vision_calls: Mapped[int] = mapped_column(Integer, default=0)
    initiative_calls: Mapped[int] = mapped_column(Integer, default=0)

class UserMemory(Base):
    __tablename__ = "user_memories"
    __table_args__ = (UniqueConstraint("user_id", "memory_key", name="uq_user_memory_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    memory_key: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40), default="general")
    value: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(40), default="chat")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UserCoachRules(Base):
    __tablename__ = "user_coach_rules"

    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), primary_key=True)
    rules: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())




class UserNutritionGoal(Base):
    __tablename__ = "user_nutrition_goals"

    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), primary_key=True)
    calorie_target_kcal: Mapped[int] = mapped_column(Integer, default=1500)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DailyChallengePlan(Base):
    __tablename__ = "daily_challenge_plans"
    __table_args__ = (UniqueConstraint("user_id", "challenge_date", name="uq_daily_challenge_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    challenge_date: Mapped[date] = mapped_column(Date, index=True)
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    selected: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="choosing", index=True)
    reward_xp: Mapped[int] = mapped_column(Integer, default=60)
    reward_coins: Mapped[int] = mapped_column(Integer, default=15)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EveningNutritionReview(Base):
    __tablename__ = "evening_nutrition_reviews"
    __table_args__ = (UniqueConstraint("user_id", "review_date", name="uq_evening_review_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    review_date: Mapped[date] = mapped_column(Date, index=True)
    text: Mapped[str] = mapped_column(Text)
    emotion: Mapped[str] = mapped_column(String(30), default="neutral")
    tomorrow_focus: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

