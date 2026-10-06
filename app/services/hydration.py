from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyStat, UserProfile
from app.services.rewards import apply_reward

HYDRATION_REWARD_LIMIT = 3


async def record_hydration(
    session: AsyncSession,
    profile: UserProfile,
    stat: DailyStat,
    *,
    source: str,
) -> bool:
    """Record one hydration serving.

    DailyStat.water is kept for DB compatibility, but from V13 it means a
    hydration mark: plain water OR a zero/very-low-calorie non-alcoholic drink.
    """
    stat.water += 1
    rewarded = stat.water <= HYDRATION_REWARD_LIMIT
    if rewarded:
        await apply_reward(
            session,
            profile,
            event_type="hydration",
            xp=3,
            bond=1,
            payload={"source": source, "hydration_count": stat.water},
        )
    else:
        await apply_reward(
            session,
            profile,
            event_type="hydration_unrewarded",
            payload={"source": source, "hydration_count": stat.water},
        )
    return rewarded
