"""Time-limited activity accommodations for Selin & Tori.

A break is never automatically lifted: on the check-in date Selin asks the player
whether their real-world restrictions still apply. Food, story and Tori remain active.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from zoneinfo import ZoneInfo

from app.config import settings
from app.db import Base, SessionLocal
from app.models import DailyChallengePlan, Notification, UserProfile

TZ = ZoneInfo(settings.timezone)
RESTRICTED_CODES = frozenset({"workout", "steps"})
SUPPRESSED_NOTIFICATION_KINDS = frozenset({
    "monday_start", "monday_finish", "thursday_start", "thursday_second", "activity_nudge"
})


class ActivityBreak(Base):
    __tablename__ = "activity_breaks"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    check_on: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(String(80), nullable=False, default="rest")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    checkin_sent_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True)
class BreakProposal:
    days: int
    reason: str


_WORDS = {"один": 1, "одну": 1, "одна": 1, "две": 2, "два": 2,
          "три": 3, "четыре": 4, "пять": 5, "шесть": 6}
_DURATION = re.compile(
    r"\b(?P<n>\d{1,3}|один|одну|одна|две|два|три|четыре|пять|шесть)"
    r"\s*(?P<u>дн(?:я|ей|ь)|сут(?:ки|ок)|недел(?:ю|и|ь)|месяц(?:а|ев)?)\b",
    re.I,
)
_RESTRICTION = re.compile(
    r"\b(?:без\s+(?:спорта|тренировок|зала|нагрузок|физических\s+нагрузок)|"
    r"не\s+(?:(?:могу|буду|стоит)\s+)?(?:ходить\s+в\s+зал|тренироваться|заниматься\s+спортом)|"
    r"(?:не\s+тренируюсь|отменяю\s+тренировки|приостанавливаю\s+тренировки)|"
    r"нельзя\s+(?:ходить\s+в\s+зал|тренироваться|заниматься\s+спортом|физические\s+нагрузки)|"
    r"(?:спорт|тренировки|зал|нагрузки)\s+(?:под\s+запретом|пока\s+нельзя))\b",
    re.I,
)


# Colloquial variants: "na trenirovki ne budu hodit" / "ne poidu v zal".
_COLLOQUIAL = re.compile(
    r"(?:\b(?:на\s+тренировк[а-я]*|в\s+зал|заниматься\s+спортом)"
    r"\s+(?:(?:я|пока|вообще|больше)\s+){0,3}не\s+"
    r"(?:буду|пойду|хожу|смогу|попаду)\b|"
    r"\bне\s+(?:буду|пойду|хожу|смогу|попаду)\b"
    r"(?:\s+[а-я]+){0,6}\s+(?:на\s+тренировк[а-я]*|в\s+зал|тренироваться)\b)",
    re.I,
)
_NEAR_WEEK = re.compile(
    r"\b(?:в|на)?\s*(?:ближайшую|следующую|эту|целую|предстоящую)\s+неделю\b"
    r"|\bв\s+течение\s+недели\b"
    r"|\b(?:одну\s+неделю|на\s+неделю|неделю\s+(?:не\s+буду|без\s+спорта))\b", re.I,
)


def parse_break_proposal(text: str) -> BreakProposal | None:
    """Only overt statements *with a duration*; never infer a medical diagnosis."""
    src = str(text or "").lower().strip()
    if not src or src.startswith("/") or "?" in src or "？" in src:
        return None
    if re.search(r"\bне\s+могу\s+без\s+(?:спорта|тренировок)\b", src):
        return None
    if not (_RESTRICTION.search(src) or _COLLOQUIAL.search(src)):
        return None
    match = _DURATION.search(src)
    if match:
        num = match.group("n")
        qty = int(num) if num.isdigit() else _WORDS.get(num, 0)
        unit = match.group("u")
        days = qty * (7 if unit.startswith("недел") else 30 if unit.startswith("месяц") else 1)
    elif _NEAR_WEEK.search(src):
        days = 7
    else:
        return None
    if not 1 <= days <= 90:
        return None
    reason = "tattoo" if "тату" in src else "rest"
    return BreakProposal(days=days, reason=reason)


def now_local() -> datetime:
    return datetime.now(TZ)


def reason_label(reason: str) -> str:
    return "после татуировки" if reason == "tattoo" else "из-за временного ограничения нагрузок"


def is_in_effect(row: ActivityBreak | None, day: date) -> bool:
    return bool(row and row.status in {"active", "awaiting_confirmation"} and day >= row.starts_on)


async def get_break(session: AsyncSession, user_id: int) -> ActivityBreak | None:
    return await session.get(ActivityBreak, user_id)


async def is_paused(session: AsyncSession, user_id: int, day: date | None = None) -> bool:
    row = await get_break(session, user_id)
    return is_in_effect(row, day or now_local().date())


async def start_break(
    session: AsyncSession, user_id: int, days: int, reason: str = "rest",
    *, today: date | None = None,
) -> ActivityBreak:
    if not 1 <= int(days) <= 90:
        raise ValueError("Duration must be 1–90 days")
    today = today or now_local().date()
    row = await get_break(session, user_id)
    if row is None:
        row = ActivityBreak(user_id=user_id, starts_on=today,
                            check_on=today + timedelta(days=int(days)), reason=reason,
                            status="active", updated_at=now_local())
        session.add(row)
    else:
        row.starts_on = today
        row.check_on = today + timedelta(days=int(days))
        row.reason = reason if reason in {"tattoo", "rest"} else "rest"
        row.status = "active"
        row.checkin_sent_on = None
        row.updated_at = now_local()
    return row


async def resume_break(session: AsyncSession, user_id: int) -> bool:
    row = await get_break(session, user_id)
    if not row or row.status == "resumed":
        return False
    row.status = "resumed"
    row.updated_at = now_local()
    return True


async def extend_break(
    session: AsyncSession, user_id: int, days: int = 7,
    *, today: date | None = None,
) -> ActivityBreak | None:
    if not 1 <= int(days) <= 90:
        raise ValueError("Duration must be 1–90 days")
    row = await get_break(session, user_id)
    if not row or row.status == "resumed":
        return None
    today = today or now_local().date()
    row.check_on = max(today, row.check_on) + timedelta(days=int(days))
    row.status = "active"
    row.checkin_sent_on = None
    row.updated_at = now_local()
    return row


_RECOVERY_POOL = (
    {"code": "tori_interactions", "title": "Лисий привал", "description": "Пообщайся с Тори двумя разными способами.", "target": 2, "why": "Пусть сегодня будет без спортивных обязанностей."},
    {"code": "food_logs", "title": "Журнал припасов", "description": "Запиши два приёма пищи или продукта.", "target": 2, "why": "Это помогает заметить привычки без лишней нагрузки."},
    {"code": "water", "title": "Фляга рядом", "description": "Отметь три порции воды или почти некалорийных напитков.", "target": 3, "why": "Необязательное небольшое дело на день."},
    {"code": "tori_bond", "title": "Время для стаи", "description": "Получи сегодня +2 связи с Тори.", "target": 2, "why": "Приключения не всегда требуют тренировки."},
    {"code": "meals", "title": "Три остановки", "description": "Отметь три полноценных приёма пищи.", "target": 3, "why": "Обычные дела важны и в период отдыха."},
    {"code": "quests", "title": "Маленькое дело", "description": "Закрой один посильный квест, не связанный со спортом.", "target": 1, "why": "Без обязательных физических заданий."},
)


def adapt_challenge_plan(plan: DailyChallengePlan) -> bool:
    """Replace unsafe offers. Unselect active sports without marking them failed.

    Previously completed/failed challenge history and rewards are never modified.
    """
    from app.services.challenges import challenge_state

    # Respect a day the user has already manually finalized.
    if plan.status in {"completed", "failed", "closed"}:
        return False
    options = [dict(o) for o in (plan.options or [])]
    if not options:
        return False
    state = challenge_state(plan)
    completed = set(state["completed"])
    failed = set(state["failed"])
    changing = [i for i, opt in enumerate(options)
                if opt.get("code") in RESTRICTED_CODES and i not in completed and i not in failed]
    if not changing:
        return False

    used = {opt.get("code") for i, opt in enumerate(options) if i not in changing}
    for index in changing:
        replacement = next((o for o in _RECOVERY_POOL if o["code"] not in used), None)
        if replacement is None:
            # No invented new challenge types; the existing supported codes suffice for 3 slots.
            continue
        options[index] = dict(replacement)
        used.add(replacement["code"])
        # A substituted challenge is optional; it must be accepted explicitly.
        state["accepted"] = [i for i in state["accepted"] if i != index]
    plan.options = options
    plan.selected = {name: sorted(set(state[name])) for name in ("accepted", "completed", "failed")}
    if not any(i in state["accepted"] and i not in completed and i not in failed
               for i in range(len(options))):
        plan.status = "choosing" if len(completed) < len(options) else "completed"
    return True


async def adapt_today_challenges(session: AsyncSession, user_id: int, day: date) -> bool:
    plan = await session.scalar(select(DailyChallengePlan).where(
        DailyChallengePlan.user_id == user_id, DailyChallengePlan.challenge_date == day
    ))
    return adapt_challenge_plan(plan) if plan else False


async def suppress_due_sport_notifications(now: datetime) -> int:
    """Suppress before original scheduler can send already-enqueued sport reminders."""
    suppressed = 0
    async with SessionLocal() as session:
        rows = list((await session.scalars(
            select(Notification).where(
                Notification.sent_at.is_(None),
                Notification.scheduled_at <= now,
                Notification.kind.in_(SUPPRESSED_NOTIFICATION_KINDS),
            ).order_by(Notification.scheduled_at).limit(500)
        )).all())
        by_user: dict[int, ActivityBreak | None] = {}
        for n in rows:
            if n.user_id not in by_user:
                by_user[n.user_id] = await get_break(session, n.user_id)
            if is_in_effect(by_user[n.user_id], n.scheduled_at.date()):
                n.sent_at = now
                suppressed += 1
        await session.commit()
    return suppressed


async def send_due_checkins(bot, now: datetime) -> None:
    """One non-pushy check-in, after 11:00 local time; no automatic return."""
    if now.hour < 11:
        return
    async with SessionLocal() as session:
        due = list((await session.scalars(
            select(ActivityBreak).where(
                ActivityBreak.status == "active",
                ActivityBreak.check_on <= now.date(),
                ActivityBreak.checkin_sent_on.is_(None),
            ).limit(50)
        )).all())
        for row in due:
            user = await session.get(UserProfile, row.user_id)
            if user is None:
                continue
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Можно вернуться", callback_data=f"activity:resume:{user.telegram_id}:{row.check_on:%Y%m%d}")],
                [InlineKeyboardButton(text="🌿 Продлить на 7 дней", callback_data=f"activity:extend:{user.telegram_id}:{row.check_on:%Y%m%d}")],
            ])
            try:
                await bot.send_message(
                    user.telegram_id,
                    "Селин: — Мы договаривались проверить, можно ли возвращаться к тренировкам. "
                    "Ограничения уже сняты? Если нет — я продолжу не напоминать о спорте. "
                    "Ориентируйся на рекомендации специалиста или тату-мастера.",
                    reply_markup=markup,
                )
            except Exception as exc:
                print("[ACTIVITY_BREAK] checkin send failed:", type(exc).__name__)
                continue
            row.checkin_sent_on = now.date()
            row.status = "awaiting_confirmation"
            row.updated_at = now
            await session.commit()
