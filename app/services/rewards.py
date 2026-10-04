from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyStat, GameEvent, UserProfile


async def get_or_create_profile(session: AsyncSession, telegram_id: int, display_name: str | None) -> UserProfile:
    profile = await session.scalar(select(UserProfile).where(UserProfile.telegram_id == telegram_id))
    if profile:
        if display_name and profile.display_name != display_name:
            profile.display_name = display_name
        return profile

    profile = UserProfile(telegram_id=telegram_id, display_name=display_name)
    session.add(profile)
    await session.flush()
    return profile


async def get_or_create_daily(session: AsyncSession, user_id: int, day: date) -> DailyStat:
    stat = await session.scalar(
        select(DailyStat).where(DailyStat.user_id == user_id, DailyStat.stat_date == day)
    )
    if stat:
        return stat
    stat = DailyStat(user_id=user_id, stat_date=day)
    session.add(stat)
    await session.flush()
    return stat


async def apply_reward(
    session: AsyncSession,
    profile: UserProfile,
    *,
    event_type: str,
    xp: int = 0,
    coins: int = 0,
    bond: int = 0,
    strength: int = 0,
    willpower: int = 0,
    atelier_dust: int = 0,
    payload: dict[str, Any] | None = None,
) -> None:
    profile.xp += xp
    profile.coins += coins
    profile.tori_bond += bond
    profile.strength += strength
    profile.willpower += willpower
    profile.atelier_dust += atelier_dust

    session.add(
        GameEvent(
            user_id=profile.id,
            event_type=event_type,
            xp_delta=xp,
            coins_delta=coins,
            atelier_dust_delta=atelier_dust,
            payload={
                **(payload or {}),
                "tori_bond_delta": bond,
                "strength_delta": strength,
                "willpower_delta": willpower,
            },
        )
    )
