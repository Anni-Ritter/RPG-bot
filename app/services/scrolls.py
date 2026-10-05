from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ScrollDefinition, ScrollInventory, UserProfile


SCROLL_TARGET_LABELS = {
    "outfit": "Селин",
    "accessory": "Селин",
    "tori": "Тори",
    "background": "Фон",
}

SCROLL_TYPE_LABELS = {
    "outfit": "Полный образ",
    "accessory": "Аксессуар",
    "tori": "Образ фамильяра",
    "background": "Фон / сцена",
}


def get_scroll_target(scroll: ScrollDefinition) -> str:
    return SCROLL_TARGET_LABELS.get(scroll.item_type, "Неизвестно")


def get_scroll_type_label(scroll: ScrollDefinition) -> str:
    return SCROLL_TYPE_LABELS.get(scroll.item_type, scroll.item_type)

DUPLICATE_DUST = {
    "common": 10,
    "rare": 25,
    "epic": 60,
    "legendary": 150,
}


async def grant_scroll(
    session: AsyncSession,
    profile: UserProfile,
    scroll_id: str,
    *,
    status: str = "sealed",
) -> dict:
    scroll = await session.get(ScrollDefinition, scroll_id)
    if not scroll:
        raise ValueError(f"Неизвестный свиток: {scroll_id}")

    owned = await session.scalar(
        select(ScrollInventory).where(
            ScrollInventory.user_id == profile.id,
            ScrollInventory.scroll_id == scroll_id,
        )
    )

    if owned:
        dust = DUPLICATE_DUST[scroll.rarity]
        profile.atelier_dust += dust
        return {"scroll": scroll, "duplicate": True, "dust": dust, "inventory": owned}

    inventory = ScrollInventory(
        user_id=profile.id,
        scroll_id=scroll.id,
        status=status,
    )
    session.add(inventory)
    await session.flush()
    return {"scroll": scroll, "duplicate": False, "dust": 0, "inventory": inventory}
