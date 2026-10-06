from __future__ import annotations

from datetime import date
from typing import Any

from aiogram.types import Message

from app.db import SessionLocal
from app.services.assets import send_reaction
from app.services.challenges import challenge_state, evaluate_plan, get_plan, now_local
from app.services.rewards import get_or_create_profile


async def check_and_notify_challenges(
    message: Message,
    telegram_id: int,
    full_name: str | None,
    *,
    day: date | None = None,
) -> dict[str, Any] | None:
    """Evaluate today's accepted challenges and immediately announce new completions.

    Call this only after the action that may advance progress has already been committed.
    Limit-style challenges are intentionally finalized only by the evening review.
    """
    day = day or now_local().date()

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, telegram_id, full_name)
        plan = await get_plan(session, profile.id, day)
        if plan is None:
            await session.commit()
            return None

        before = set(challenge_state(plan)["completed"])
        _, values, chests = await evaluate_plan(session, profile, plan, final=False)
        after_state = challenge_state(plan)
        newly_completed = [i for i in after_state["completed"] if i not in before]

        if not newly_completed:
            await session.commit()
            return None

        options = list(plan.options or [])
        items: list[dict[str, Any]] = []
        for index in newly_completed:
            if 0 <= index < len(options):
                option = dict(options[index])
                items.append(
                    {
                        "index": index,
                        "title": str(option.get("title") or "Испытание"),
                        "description": str(option.get("description") or ""),
                        "value": values.get(index),
                    }
                )

        reward_xp = int(plan.reward_xp or 0) * len(items)
        reward_coins = int(plan.reward_coins or 0) * len(items)
        await session.commit()

    if not items:
        return None

    if len(items) == 1:
        lines = ["🎯 Испытание выполнено!", f"✅ {items[0]['title']}"]
    else:
        lines = [f"🎯 Выполнено испытаний: {len(items)}"]
        lines.extend(f"✅ {item['title']}" for item in items)

    lines.append(f"+{reward_xp} XP · +{reward_coins} монет")
    if chests:
        lines.append(f"🎁 Сундук испытания: +{chests}")
    lines.append("")
    lines.append("— Вот. Теперь это можно считать закрытым.")

    await send_reaction(message, "selin", "triumphant", "\n".join(lines))
    return {
        "items": items,
        "reward_xp": reward_xp,
        "reward_coins": reward_coins,
        "chests": chests,
    }
