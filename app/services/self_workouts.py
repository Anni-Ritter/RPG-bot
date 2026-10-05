from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import GameEvent, WorkoutSession

TZ = ZoneInfo(settings.timezone)

WORKOUT_KIND_LABELS = {
    "strength": "Силовая",
    "pilates": "Пилатес / растяжка",
    "cardio": "Кардио",
    "other": "Другое",
}


@dataclass(frozen=True)
class WorkoutReward:
    xp: int
    coins: int


def reward_for_minutes(minutes: int) -> WorkoutReward:
    if minutes < 10:
        return WorkoutReward(0, 0)
    if minutes < 20:
        return WorkoutReward(20, 4)
    if minutes < 40:
        return WorkoutReward(35, 7)
    if minutes < 60:
        return WorkoutReward(50, 10)
    return WorkoutReward(55, 11)


SELIN_REACTIONS = {
    "tiny": (
        "Селин: — Записала. До полноценной награды чуть не хватило, но движение всё равно считается.",
        "Селин: — Коротко. Но лучше так, чем совсем ничего.",
    ),
    "short": (
        "Селин: — Десять минут всё ещё больше нуля. Засчитано.",
        "Селин: — Небольшая тренировка. Главное, что ты всё-таки начала.",
    ),
    "normal": (
        "Селин посмотрела на время. — Вот это уже тренировка, а не попытка договориться с совестью.",
        "Селин: — Хорошо. Этого вполне достаточно, чтобы день не прошёл мимо.",
    ),
    "long": (
        "Селин одобрительно кивнула. — Серьёзно. Мне нравится.",
        "Селин: — Вот теперь я точно не стану спрашивать, тренировалась ли ты сегодня.",
    ),
    "very_long": (
        "Селин: — Всё. Достаточно. Я сказала тренироваться, а не переселяться туда насовсем.",
        "Селин приподняла бровь. — Час? Ладно. Сегодня вопросов нет.",
    ),
}

TORI_REACTIONS = {
    "tiny": (
        "Тори заинтересованно наблюдает, будто короткая разминка всё равно была важным ритуалом.",
    ),
    "short": (
        "Тори радостно подпрыгнул. Похоже, для него десять минут уже достойны праздника.",
    ),
    "normal": (
        "Тори выглядит чрезвычайно довольным и явно присваивает себе часть заслуг.",
    ),
    "long": (
        "Тори смотрит на тебя с таким уважением, будто только что наблюдал героическую битву.",
    ),
    "very_long": (
        "Тори сначала впечатлён. Потом, кажется, начинает подозревать, что ты немного разогналась.",
    ),
}


def duration_tier(minutes: int) -> str:
    if minutes < 10:
        return "tiny"
    if minutes < 20:
        return "short"
    if minutes < 40:
        return "normal"
    if minutes < 60:
        return "long"
    return "very_long"


def pick_reaction(minutes: int) -> tuple[str, str, str]:
    tier = duration_tier(minutes)
    # Тори появляется реже, чтобы Селин оставалась основным голосом системы.
    if random.random() < 0.30:
        emotion = "proud" if minutes >= 40 else "happy"
        return "tori", emotion, random.choice(TORI_REACTIONS[tier])

    emotion = "triumphant" if minutes >= 40 else "smirk"
    if minutes < 10:
        emotion = "neutral"
    return "selin", emotion, random.choice(SELIN_REACTIONS[tier])


async def manual_reward_already_claimed(session: AsyncSession, user_id: int, day: date) -> bool:
    marker = await session.scalar(
        select(WorkoutSession.id).where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.workout_date == day,
            WorkoutSession.kind == "manual_reward",
            WorkoutSession.status == "completed",
        )
    )
    return marker is not None


async def mark_manual_reward_claimed(session: AsyncSession, user_id: int, day: date) -> None:
    existing = await session.scalar(
        select(WorkoutSession).where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.workout_date == day,
            WorkoutSession.kind == "manual_reward",
        )
    )
    if existing:
        existing.status = "completed"
        existing.completed_at = datetime.now(TZ)
        return

    now = datetime.now(TZ)
    session.add(
        WorkoutSession(
            user_id=user_id,
            workout_date=day,
            kind="manual_reward",
            status="completed",
            started_at=now,
            completed_at=now,
        )
    )


async def weekly_workout_summary(session: AsyncSession, user_id: int, today: date) -> dict:
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    manual_events = list(
        (
            await session.scalars(
                select(GameEvent).where(
                    GameEvent.user_id == user_id,
                    GameEvent.event_type == "manual_workout_complete",
                )
            )
        ).all()
    )

    manual_count = 0
    manual_minutes = 0
    by_kind = {key: 0 for key in WORKOUT_KIND_LABELS}

    for event in manual_events:
        payload = event.payload or {}
        raw_day = payload.get("workout_date")
        try:
            event_day = date.fromisoformat(raw_day) if raw_day else None
        except (TypeError, ValueError):
            event_day = None
        if not event_day or not (week_start <= event_day <= week_end):
            continue

        manual_count += 1
        manual_minutes += int(payload.get("minutes") or 0)
        kind = payload.get("kind")
        if kind in by_kind:
            by_kind[kind] += 1

    scheduled = list(
        (
            await session.scalars(
                select(WorkoutSession).where(
                    WorkoutSession.user_id == user_id,
                    WorkoutSession.workout_date >= week_start,
                    WorkoutSession.workout_date <= week_end,
                    WorkoutSession.status == "completed",
                    WorkoutSession.kind.in_(["monday", "thursday_first", "thursday_bonus"]),
                )
            )
        ).all()
    )

    scheduled_minutes = 0
    for workout in scheduled:
        if workout.started_at and workout.completed_at:
            try:
                seconds = (workout.completed_at - workout.started_at).total_seconds()
                if seconds > 0:
                    scheduled_minutes += int(round(seconds / 60))
            except (TypeError, ValueError):
                pass

    return {
        "count": manual_count + len(scheduled),
        "minutes": manual_minutes + scheduled_minutes,
        "manual_count": manual_count,
        "scheduled_count": len(scheduled),
        "by_kind": by_kind,
    }


async def daily_workout_minutes(session: AsyncSession, user_id: int, day: date) -> int:
    """Best-effort total workout minutes for one day from manual + scheduled sessions."""
    total = 0
    manual_events = list(
        (
            await session.scalars(
                select(GameEvent).where(
                    GameEvent.user_id == user_id,
                    GameEvent.event_type == "manual_workout_complete",
                )
            )
        ).all()
    )
    for event in manual_events:
        payload = event.payload or {}
        if payload.get("workout_date") == day.isoformat():
            total += int(payload.get("minutes") or 0)

    scheduled = list(
        (
            await session.scalars(
                select(WorkoutSession).where(
                    WorkoutSession.user_id == user_id,
                    WorkoutSession.workout_date == day,
                    WorkoutSession.status == "completed",
                    WorkoutSession.kind.in_(["monday", "thursday_first", "thursday_bonus"]),
                )
            )
        ).all()
    )
    for workout in scheduled:
        if workout.started_at and workout.completed_at:
            try:
                seconds = (workout.completed_at - workout.started_at).total_seconds()
                if seconds > 0:
                    total += int(round(seconds / 60))
            except (TypeError, ValueError):
                pass
    return total
