from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyStat, GameEvent, UserAchievement, UserProfile
from app.services.scrolls import grant_scroll


ACHIEVEMENTS = {
    "first_10k": {
        "title": "Есть ноги — надо пользоваться",
        "category": "🚶 Шаги",
        "condition": "Пройти 10 000 шагов за один день.",
        "reward_text": "Скрытая награда для Тори",
        "scroll_id": "s06",
        "progress_kind": "best_steps",
        "target": 10_000,
    },
    "first_25k": {
        "title": "Ты куда разогналась?",
        "category": "🚶 Шаги",
        "condition": "Пройти 25 000 шагов за один день.",
        "reward_text": "+50 монет · +50 Пыли ателье",
        "coins": 50,
        "dust": 50,
        "progress_kind": "best_steps",
        "target": 25_000,
    },
    "first_manual_workout": {
        "title": "Арена где угодно",
        "category": "🏋️ Тренировки",
        "condition": "Завершить хотя бы одну самостоятельную тренировку.",
        "reward_text": "+20 монет",
        "coins": 20,
        "progress_kind": "event_count",
        "event_type": "manual_workout_complete",
        "target": 1,
    },
    "three_bonus_thursdays": {
        "title": "Не ушла",
        "category": "🏋️ Тренировки",
        "condition": "Трижды остаться на вторую тренировку в четверг.",
        "reward_text": "Скрытая награда для Тори",
        "scroll_id": "s23",
        "progress_kind": "event_count",
        "event_type": "thursday_bonus_complete",
        "target": 3,
    },
    "four_mondays": {
        "title": "Понедельники больше не обсуждаем",
        "category": "🏋️ Тренировки",
        "condition": "Завершить четыре понедельничные тренировки.",
        "reward_text": "Запечатанный свиток",
        "scroll_id": "s30",
        "progress_kind": "event_count",
        "event_type": "monday_workout_complete",
        "target": 4,
    },
    "tori_bond_50": {
        "title": "Он тебя выбрал",
        "category": "🦊 Тори",
        "condition": "Довести связь с Тори до 50 через взаимодействия, гидратацию и события.",
        "reward_text": "Скрытая награда для Тори",
        "scroll_id": "s14",
        "progress_kind": "tori_bond",
        "target": 50,
    },
    "tori_bond_100": {
        "title": "Теперь вы точно стая",
        "category": "🦊 Тори",
        "condition": "Довести связь с Тори до 100 через взаимодействия, гидратацию и события.",
        "reward_text": "Секретный свиток Тори",
        "scroll_id": "s28",
        "progress_kind": "tori_bond",
        "target": 100,
    },
    "four_bonus_thursdays": {
        "title": "Четыре двери",
        "category": "🏋️ Тренировки",
        "condition": "Четырежды остаться на вторую тренировку в четверг.",
        "reward_text": "Секретный легендарный свиток",
        "scroll_id": "s07",
        "progress_kind": "event_count",
        "event_type": "thursday_bonus_complete",
        "target": 4,
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


async def achievement_catalog(
    session: AsyncSession,
    profile: UserProfile,
) -> list[dict]:
    unlocked_codes = set(
        (await session.scalars(
            select(UserAchievement.code).where(UserAchievement.user_id == profile.id)
        )).all()
    )

    best_steps = int(
        await session.scalar(
            select(func.max(DailyStat.steps)).where(DailyStat.user_id == profile.id)
        )
        or 0
    )
    event_cache: dict[str, int] = {}
    rows: list[dict] = []

    for code, spec in ACHIEVEMENTS.items():
        kind = spec.get("progress_kind")
        target = int(spec.get("target", 1))
        current = 0
        progress_label = ""

        if kind == "best_steps":
            current = min(best_steps, target)
            progress_label = f"{current:,} / {target:,} шагов (лучший день)".replace(",", " ")
        elif kind == "tori_bond":
            current = min(profile.tori_bond, target)
            progress_label = f"{current} / {target} связи"
        elif kind == "event_count":
            event_type = str(spec.get("event_type", ""))
            if event_type not in event_cache:
                event_cache[event_type] = await _count_events(session, profile.id, event_type)
            current = min(event_cache[event_type], target)
            progress_label = f"{current} / {target}"

        rows.append({
            "code": code,
            "spec": spec,
            "unlocked": code in unlocked_codes,
            "current": current,
            "target": target,
            "progress_label": progress_label,
        })

    return rows


def achievement_catalog_text(rows: list[dict]) -> str:
    unlocked_count = sum(1 for row in rows if row["unlocked"])
    lines = [
        "🏆 Ачивки",
        f"Открыто: {unlocked_count}/{len(rows)}",
        "",
        "Условия не скрыты: можно заранее посмотреть, к чему идти. Награда выдаётся автоматически, когда условие выполнено.",
    ]

    category_order = ["🚶 Шаги", "🏋️ Тренировки", "🦊 Тори"]
    categories = {name: [] for name in category_order}
    other: list[dict] = []
    for row in rows:
        category = str(row["spec"].get("category", "Прочее"))
        if category in categories:
            categories[category].append(row)
        else:
            other.append(row)

    grouped = [(name, categories[name]) for name in category_order if categories[name]]
    if other:
        grouped.append(("✨ Прочее", other))

    for category, category_rows in grouped:
        lines.extend(["", category])
        for row in category_rows:
            spec = row["spec"]
            status = "✅" if row["unlocked"] else "🔒"
            lines.append(f"{status} {spec['title']}")
            lines.append(f"Как получить: {spec['condition']}")
            if row["unlocked"]:
                lines.append("Статус: получено")
            elif row["progress_label"]:
                lines.append(f"Прогресс: {row['progress_label']}")
            lines.append(f"Награда: {spec['reward_text']}")

    return "\n".join(lines)


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
