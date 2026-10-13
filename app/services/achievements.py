from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyStat, FoodNutritionLog, GameEvent, UserAchievement, UserProfile
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


# Short-term achievements supplement the existing long-term milestones.
# Existing codes and unlocked achievements never change.
ACHIEVEMENTS.update({
    "first_3k": {
        "title": "Размяла ноги", "category": "🚶 Шаги",
        "condition": "Пройти 3 000 шагов за день.", "reward_text": "+5 монет",
        "coins": 5, "progress_kind": "best_steps", "target": 3000,
    },
    "first_7k": {
        "title": "Разведка удалась", "category": "🚶 Шаги",
        "condition": "Пройти 7 000 шагов за день.", "reward_text": "+12 монет",
        "coins": 12, "progress_kind": "best_steps", "target": 7000,
    },
    "tori_bond_5": {
        "title": "Любопытный хвост", "category": "🦊 Тори",
        "condition": "Довести связь с Тори до 5.", "reward_text": "+5 монет",
        "coins": 5, "progress_kind": "tori_bond", "target": 5,
    },
    "tori_bond_15": {
        "title": "Уже почти свои", "category": "🦊 Тори",
        "condition": "Довести связь с Тори до 15.", "reward_text": "+12 монет",
        "coins": 12, "progress_kind": "tori_bond", "target": 15,
    },
    "manual_3": {
        "title": "Арена становится привычкой", "category": "🏋️ Тренировки",
        "condition": "Записать 3 самостоятельные тренировки.", "reward_text": "+15 монет",
        "coins": 15, "progress_kind": "event_count", "event_type": "manual_workout_complete", "target": 3,
    },
    "hydration_1": {
        "title": "Припасы пополнены", "category": "🥤 Повседневное",
        "condition": "Записать первую гидратацию (вода или zero).", "reward_text": "+3 монеты",
        "coins": 3, "progress_kind": "event_count", "event_type": "hydration", "target": 1,
    },
    "hydration_10": {
        "title": "Фляга всегда под рукой", "category": "🥤 Повседневное",
        "condition": "Получить награду за гидратацию 10 раз (за всё время).", "reward_text": "+10 монет",
        "coins": 10, "progress_kind": "event_count", "event_type": "hydration", "target": 10,
    },
    "food_log_1": {
        "title": "Первая запись в журнале", "category": "🍲 Питание",
        "condition": "Записать еду/напиток с описанием или фото и подтвердить порцию.",
        "reward_text": "+5 монет", "coins": 5, "progress_kind": "food_entry_count", "target": 1,
    },
    "food_log_5": {
        "title": "Журнал припасов", "category": "🍲 Питание",
        "condition": "Записать 5 порций еды/напитков с описанием или фото.",
        "reward_text": "+12 монет", "coins": 12, "progress_kind": "food_entry_count", "target": 5,
    },
    "challenge_1": {
        "title": "Первое испытание", "category": "🎯 Испытания",
        "condition": "Завершить один ежедневный челлендж.", "reward_text": "+7 монет",
        "coins": 7, "progress_kind": "event_count", "event_type": "daily_challenge_complete", "target": 1,
    },
    "challenge_3": {
        "title": "Серия заданий", "category": "🎯 Испытания",
        "condition": "Выполнить три ежедневных челленджа за всё время.", "reward_text": "+15 монет",
        "coins": 15, "progress_kind": "event_count", "event_type": "daily_challenge_complete", "target": 3,
    },
})



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

    # Shorter, progressively unlockable milestones (no resets or daily grind).
    best_steps = int(await session.scalar(
        select(func.max(DailyStat.steps)).where(DailyStat.user_id == profile.id)
    ) or 0)
    food_logs = int(await session.scalar(
        select(func.count(FoodNutritionLog.id)).where(FoodNutritionLog.user_id == profile.id)
    ) or 0)
    checks = {
        "first_3k": best_steps >= 3000 or (steps is not None and steps >= 3000),
        "first_7k": best_steps >= 7000 or (steps is not None and steps >= 7000),
        "tori_bond_5": profile.tori_bond >= 5,
        "tori_bond_15": profile.tori_bond >= 15,
        "manual_3": await _count_events(session, profile.id, "manual_workout_complete") >= 3,
        "hydration_1": await _count_events(session, profile.id, "hydration") >= 1,
        "hydration_10": await _count_events(session, profile.id, "hydration") >= 10,
        "food_log_1": food_logs >= 1,
        "food_log_5": food_logs >= 5,
        "challenge_1": await _count_events(session, profile.id, "daily_challenge_complete") >= 1,
        "challenge_3": await _count_events(session, profile.id, "daily_challenge_complete") >= 3,
    }
    for code, achieved in checks.items():
        if achieved:
            item = await _unlock(session, profile, code)
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
    food_entry_count = int(await session.scalar(
        select(func.count(FoodNutritionLog.id)).where(FoodNutritionLog.user_id == profile.id)
    ) or 0)

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
        elif kind == "food_entry_count":
            current = min(food_entry_count, target)
            progress_label = f"{current} / {target} записей"
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

    category_order = ["🚶 Шаги", "🏋️ Тренировки", "🍲 Питание", "🥤 Повседневное", "🦊 Тори", "🎯 Испытания"]
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
