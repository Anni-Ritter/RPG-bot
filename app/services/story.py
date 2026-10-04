
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import StoryProgress

STORY_PATH = Path(__file__).resolve().parent.parent / "game_data" / "story_days.json"
_STORY = {row["day"]: row for row in json.loads(STORY_PATH.read_text(encoding="utf-8"))}


async def get_or_create_story_progress(
    session: AsyncSession,
    user_id: int,
    today: date,
) -> StoryProgress:
    progress = await session.get(StoryProgress, user_id)
    if progress:
        return progress

    progress = StoryProgress(
        user_id=user_id,
        season_number=1,
        season_started_on=today,
        last_viewed_day=0,
        flags={},
    )
    session.add(progress)
    await session.flush()
    return progress


def story_day(progress: StoryProgress, today: date) -> int:
    raw = (today - progress.season_started_on).days + 1
    return max(1, min(raw, 28))


def story_chapter(day: int) -> int:
    return min(4, ((max(1, day) - 1) // 7) + 1)


def get_scene(day: int) -> dict:
    return _STORY[max(1, min(day, 28))]


def choice_key(day: int) -> str:
    return f"story_choice_{day}"


STORY_REWARDS = {
    7: ["s03"],
    14: ["s10"],
    18: ["s20"],
    21: ["s18"],
    26: ["s29"],
    28: ["s31", "s32"],
}


def reward_flag(day: int, scroll_id: str) -> str:
    return f"story_reward_{day}_{scroll_id}"
