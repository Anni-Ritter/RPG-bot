from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
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
