from __future__ import annotations

import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChestOpen, GachaState, ScrollDefinition, UserProfile
from app.services.scrolls import grant_scroll

RARITY_WEIGHTS = {
    "common": 45,
    "rare": 35,
    "epic": 17,
    "legendary": 3,
}


def roll_rarity(state: GachaState) -> str:
    # Четвёртый сундук сезона гарантированно Epic+, если раньше Epic+ не было.
    if state.season_chests_opened >= 3 and not state.season_epic_plus_hit:
        return random.choices(["epic", "legendary"], weights=[85, 15], k=1)[0]

    # После двух Common подряд следующий гарантированно Rare+.
    if state.common_streak >= 2:
        return random.choices(["rare", "epic", "legendary"], weights=[75, 22, 3], k=1)[0]

    return random.choices(
        list(RARITY_WEIGHTS.keys()),
        weights=list(RARITY_WEIGHTS.values()),
        k=1,
    )[0]


async def open_trial_chest(session: AsyncSession, profile: UserProfile) -> dict:
    if profile.trial_chests <= 0:
        raise ValueError("Нет доступных сундуков.")

    state = await session.get(GachaState, profile.id)
    if not state:
        state = GachaState(user_id=profile.id)
        session.add(state)
        await session.flush()

    rarity = roll_rarity(state)
    candidates = list(
        (
            await session.scalars(
                select(ScrollDefinition).where(
                    ScrollDefinition.source == "chest",
                    ScrollDefinition.rarity == rarity,
                )
            )
        ).all()
    )
    if not candidates:
        raise ValueError(f"В пуле сундука нет предметов редкости {rarity}.")

    scroll = random.choice(candidates)
    result = await grant_scroll(session, profile, scroll.id)
    profile.trial_chests -= 1

    state.season_chests_opened += 1
    if rarity == "common":
        state.common_streak += 1
    else:
        state.common_streak = 0
    if rarity in {"epic", "legendary"}:
        state.season_epic_plus_hit = True

    session.add(
        ChestOpen(
            user_id=profile.id,
            rarity=rarity,
            scroll_id=scroll.id,
            duplicate=result["duplicate"],
            atelier_dust_awarded=result["dust"],
        )
    )

    return {
        "scroll": scroll,
        "rarity": rarity,
        "duplicate": result["duplicate"],
        "dust": result["dust"],
    }
