from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GameEvent, UserAchievement, UserProfile
from app.services.scrolls import grant_scroll


ACHIEVEMENTS = {
    "first_10k": {
        "title": "Есть ноги — надо пользоваться",
        "reward_text": "Скрытая награда для Тори",
        "scroll_id": "s06",
    },
    "first_25k": {
        "title": "Ты куда разогналась?",
        "reward_text": "+50 монет · +50 Пыли ателье",
        "coins": 50,
        "dust": 50,
    },
    "first_manual_workout": {
        "title": "Арена где угодно",
        "reward_text": "+20 монет",
        "coins": 20,
    },
    "three_bonus_thursdays": {
        "title": "Не ушла",
        "reward_text": "Скрытая награда для Тори",
        "scroll_id": "s23",
    },
    "four_mondays": {
        "title": "Понедельники больше не обсуждаем",
        "reward_text": "Запечатанный свиток",
        "scroll_id": "s30",
    },
    "tori_bond_50": {
        "title": "Он тебя выбрал",
        "reward_text": "Скрытая награда для Тори",
        "scroll_id": "s14",
    },
    "tori_bond_100": {
        "title": "Теперь вы точно стая",
        "reward_text": "Секретный свиток Тори",
        "scroll_id": "s28",
    },
    "four_bonus_thursdays": {
        "title": "Четыре двери",
        "reward_text": "Секретный легендарный свиток",
        "scroll_id": "s07",
    },
}


async def _has(session: AsyncSession, user_id: int, code: str) -> bool:
    return bool(
        await session.scalar(
            select(UserAchievement.id).where(
                UserAchievement.user_id == user_id,
                UserAchievement.code == code,
            )
        )
    )


async def _unlock(session: AsyncSession, profile: UserProfile, code: str) -> dict | None:
    if await _has(session, profile.id, code):
        return None

    spec = ACHIEVEMENTS[code]
    profile.coins += spec.get("coins", 0)
    profile.atelier_dust += spec.get("dust", 0)

    scroll_result = None
    if spec.get("scroll_id"):
        scroll_result = await grant_scroll(session, profile, spec["scroll_id"])

    session.add(
        UserAchievement(
            user_id=profile.id,
            code=code,
            title=spec["title"],
            reward_text=spec["reward_text"],
        )
    )
    return {"code": code, "spec": spec, "scroll": scroll_result}


async def _count_events(session: AsyncSession, user_id: int, event_type: str) -> int:
    return int(
        await session.scalar(
            select(func.count(GameEvent.id)).where(
                GameEvent.user_id == user_id,
                GameEvent.event_type == event_type,
            )
        )
        or 0
    )


async def check_achievements(
    session: AsyncSession,
    profile: UserProfile,
    *,
    steps: int | None = None,
) -> list[dict]:
    unlocked: list[dict] = []

    if steps is not None and steps >= 10_000:
        item = await _unlock(session, profile, "first_10k")
        if item:
            unlocked.append(item)

    if steps is not None and steps >= 25_000:
        item = await _unlock(session, profile, "first_25k")
        if item:
            unlocked.append(item)

    if profile.tori_bond >= 50:
        item = await _unlock(session, profile, "tori_bond_50")
        if item:
            unlocked.append(item)

    if profile.tori_bond >= 100:
        item = await _unlock(session, profile, "tori_bond_100")
        if item:
            unlocked.append(item)

    if await _count_events(session, profile.id, "manual_workout_complete") >= 1:
        item = await _unlock(session, profile, "first_manual_workout")
        if item:
            unlocked.append(item)

    if await _count_events(session, profile.id, "monday_workout_complete") >= 4:
        item = await _unlock(session, profile, "four_mondays")
        if item:
            unlocked.append(item)

    bonus_count = await _count_events(session, profile.id, "thursday_bonus_complete")
    if bonus_count >= 3:
        item = await _unlock(session, profile, "three_bonus_thursdays")
        if item:
            unlocked.append(item)
    if bonus_count >= 4:
        item = await _unlock(session, profile, "four_bonus_thursdays")
        if item:
            unlocked.append(item)

    return unlocked


def achievement_messages(items: list[dict]) -> list[str]:
    messages = []
    for item in items:
        spec = item["spec"]
        text = f"🏆 Ачивка: {spec['title']}\n{spec['reward_text']}"
        scroll_result = item.get("scroll")
        if scroll_result:
            if scroll_result["duplicate"]:
                text += f"\nДубликат превратился в Пыль ателье: +{scroll_result['dust']}."
            else:
                text += f"\nПолучен запечатанный свиток «{scroll_result['scroll'].name}»."
        messages.append(text)
    return messages
