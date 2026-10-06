from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import GameEvent, UserProfile
from app.services.rewards import apply_reward

TZ = ZoneInfo(settings.timezone)
TORI_INTERACTION_REWARD_LIMIT = 3

TORI_BOND_HELP = (
    "🦊 Как растёт связь с Тори:\n"
    "• первые 3 разных взаимодействия с Тори за день могут дать +1 связи;\n"
    "• 💧 отметка гидратации (вода, zero-тоник, чай/напиток почти без ккал) — +1 связи, первые 3 за день;\n"
    "• если после 15-минутной паузы искушение прошло — +2 связи;\n"
    "• вторая тренировка в четверг — +5 связи;\n"
    "• некоторые события Тори тоже укрепляют связь.\n\n"
    "Для сюжетной задачи достаточно любого реального +1. Самый прямой путь — открыть 🦊 Тори и повзаимодействовать с ним."
)


def _day_bounds():
    now = datetime.now(TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end


async def rewarded_tori_interactions_today(session: AsyncSession, user_id: int) -> int:
    start, end = _day_bounds()
    count = await session.scalar(
        select(func.count(GameEvent.id)).where(
            GameEvent.user_id == user_id,
            GameEvent.event_type == "tori_interaction_bond",
            GameEvent.occurred_at >= start,
            GameEvent.occurred_at <= end,
        )
    )
    return int(count or 0)


async def reward_tori_interaction(
    session: AsyncSession,
    profile: UserProfile,
    *,
    source: str,
) -> bool:
    """Give +1 bond for up to 3 distinct Tori interactions per day."""
    start, end = _day_bounds()
    rows = list((await session.scalars(
        select(GameEvent).where(
            GameEvent.user_id == profile.id,
            GameEvent.event_type == "tori_interaction_bond",
            GameEvent.occurred_at >= start,
            GameEvent.occurred_at <= end,
        )
    )).all())
    used_sources = {str((row.payload or {}).get("source") or "") for row in rows}
    if source in used_sources or len(rows) >= TORI_INTERACTION_REWARD_LIMIT:
        return False
    await apply_reward(
        session,
        profile,
        event_type="tori_interaction_bond",
        bond=1,
        payload={"source": source},
    )
    return True
