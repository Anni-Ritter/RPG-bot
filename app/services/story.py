from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Notification, StoryProgress, UserProfile
from app.services.content import game_data_path
from app.services.levels import level_from_xp

TZ = ZoneInfo(settings.timezone)

_STORY = {row["day"]: row for row in json.loads(game_data_path("story_days.json").read_text(encoding="utf-8"))}
_WEEK1 = json.loads(game_data_path("week1_v2.json").read_text(encoding="utf-8"))


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


def _flags(progress: StoryProgress) -> dict:
    return dict(progress.flags or {})


def initialize_week1_v2(progress: StoryProgress, profile: UserProfile, today: date, *, force: bool = False) -> None:
    """Initialize the interactive week-one flow without a schema migration.

    All new state lives in StoryProgress.flags, so an existing SQLite save keeps
    working. `force=True` is used by the private /restart_week1 test command.
    """
    flags = _flags(progress)
    if flags.get("week1_v2_initialized") and not force:
        return

    # Do not drag a player who is already beyond week one back to day 1 unless
    # they explicitly use the test reset command.
    if progress.last_viewed_day > 7 and not force:
        return

    keep = {
        k: v
        for k, v in flags.items()
        if not k.startswith("v2_") and not k.startswith("week1_v2")
    }
    run_id = int(flags.get("week1_v2_run", 0)) + 1
    keep.update(
        {
            "week1_v2_initialized": True,
            "week1_v2_run": run_id,
            "v2_active_day": 1,
            "v2_phase": "intro",
            "v2_day_unlocked_on": today.isoformat(),
            "v2_affinity": 0,
        }
    )
    progress.flags = keep
    progress.season_started_on = today
    progress.last_viewed_day = 0


def advance_week1_if_due(progress: StoryProgress, today: date) -> None:
    flags = _flags(progress)
    if not flags.get("week1_v2_initialized"):
        return

    day = int(flags.get("v2_active_day", 1))
    if day > 7:
        return

    completed_on = flags.get(f"v2_day_completed_on_{day}")
    completed = bool(flags.get(f"v2_day_completed_{day}"))
    if not completed or not completed_on:
        return

    if today <= date.fromisoformat(completed_on):
        return

    if day < 7:
        flags["v2_active_day"] = day + 1
        flags["v2_phase"] = "intro"
        flags["v2_day_unlocked_on"] = today.isoformat()
    else:
        flags["week1_v2_completed_on"] = completed_on
        flags["v2_active_day"] = 8
        flags["v2_phase"] = "intro"
    progress.flags = flags


def story_day(progress: StoryProgress, today: date) -> int:
    flags = _flags(progress)
    if flags.get("week1_v2_initialized"):
        active = int(flags.get("v2_active_day", 1))
        if active <= 7:
            return active
        completed_on = flags.get("week1_v2_completed_on")
        if completed_on:
            delta = max(0, (today - date.fromisoformat(completed_on)).days)
            return max(7, min(28, 7 + delta))

    raw = (today - progress.season_started_on).days + 1
    return max(1, min(raw, 28))


def story_chapter(day: int) -> int:
    return min(4, ((max(1, day) - 1) // 7) + 1)


def get_scene(day: int) -> dict:
    day = max(1, min(day, 28))
    if day <= 7:
        row = dict(_WEEK1[str(day)])
        row.update({"day": day, "chapter": 1})
        return row
    return _STORY[day]


def choice_key(day: int) -> str:
    return f"story_choice_{day}"


# Week one reward is granted only after actually clearing the chapter now.
STORY_REWARDS = {
    14: ["s10"],
    18: ["s20"],
    21: ["s18"],
    26: ["s29"],
    28: ["s31", "s32"],
}


def reward_flag(day: int, scroll_id: str) -> str:
    return f"story_reward_{day}_{scroll_id}"


def week1_active(progress: StoryProgress, today: date) -> bool:
    flags = _flags(progress)
    return bool(flags.get("week1_v2_initialized")) and story_day(progress, today) <= 7


def week1_phase(progress: StoryProgress) -> str:
    return str(_flags(progress).get("v2_phase", "intro"))


def set_week1_phase(progress: StoryProgress, phase: str) -> None:
    flags = _flags(progress)
    flags["v2_phase"] = phase
    progress.flags = flags


def add_affinity(progress: StoryProgress, delta: int) -> int:
    flags = _flags(progress)
    value = int(flags.get("v2_affinity", 0)) + delta
    flags["v2_affinity"] = value
    progress.flags = flags
    return value


def affinity_value(progress: StoryProgress) -> int:
    return int(_flags(progress).get("v2_affinity", 0))


def affinity_label(progress: StoryProgress) -> str:
    value = affinity_value(progress)
    if value <= 0:
        return "относится настороженно"
    if value <= 2:
        return "присматривается к тебе"
    if value <= 5:
        return "привыкла к твоему присутствию"
    if value <= 8:
        return "доверяет тебе"
    return "сильно к тебе привязалась"


def tori_bond_label(bond: int) -> str:
    if bond < 5:
        return "только знакомится с тобой"
    if bond < 20:
        return "начинает доверять"
    if bond < 50:
        return "явно считает тебя своей"
    if bond < 100:
        return "очень к тебе привязан"
    return "считает вас одной стаей"


def next_sync_unlock(level: int) -> str:
    return {
        1: "уровень 2 — откроются 💬 разговоры с Селин",
        2: "уровень 3 — Печать станет стабильнее в сложных сценах",
        3: "уровень 4 — откроются более личные разговоры",
        4: "уровень 5 — появятся новые варианты использования Печати",
    }.get(level, "следующие уровни продолжают усиливать связь")


def begin_objective(progress: StoryProgress, profile: UserProfile, day: int) -> None:
    flags = _flags(progress)
    flags["v2_phase"] = "objective"
    flags.pop(f"v2_objective_ready_{day}", None)
    flags.pop(f"v2_checkpoint_queued_{day}", None)
    if day == 1:
        flags["v2_day1_xp_baseline"] = profile.xp
    elif day == 2:
        flags["v2_day2_bond_baseline"] = profile.tori_bond
    progress.flags = flags


def objective_ready(profile: UserProfile, progress: StoryProgress, day: int) -> bool:
    flags = _flags(progress)
    if flags.get(f"v2_objective_ready_{day}"):
        return True
    if day == 1:
        return profile.xp - int(flags.get("v2_day1_xp_baseline", profile.xp)) >= 50
    if day == 2:
        return profile.tori_bond - int(flags.get("v2_day2_bond_baseline", profile.tori_bond)) >= 1
    if day == 3:
        level, _, _ = level_from_xp(profile.xp)
        return level >= 2
    return False


def objective_progress_text(profile: UserProfile, progress: StoryProgress, day: int) -> str:
    flags = _flags(progress)
    if day == 1:
        gained = max(0, profile.xp - int(flags.get("v2_day1_xp_baseline", profile.xp)))
        return f"Стабилизация Печати: {min(gained, 50)} / 50 XP"
    if day == 2:
        gained = max(0, profile.tori_bond - int(flags.get("v2_day2_bond_baseline", profile.tori_bond)))
        return f"Новая связь с Тори: {min(gained, 1)} / 1"
    if day == 3:
        level, _, next_threshold = level_from_xp(profile.xp)
        if level >= 2:
            return "Синхронизация: уровень 2 достигнут"
        return f"Синхронизация: {profile.xp} / {next_threshold or 250} XP"
    if day == 4:
        return "Активность: нужны 5 000 шагов или любая тренировка"
    return ""


def mark_objective_ready(progress: StoryProgress, day: int) -> None:
    flags = _flags(progress)
    flags[f"v2_objective_ready_{day}"] = True
    progress.flags = flags


def mark_day_complete(progress: StoryProgress, day: int, today: date) -> None:
    flags = _flags(progress)
    flags[f"v2_day_completed_{day}"] = True
    flags[f"v2_day_completed_on_{day}"] = today.isoformat()
    flags["v2_phase"] = "complete"
    progress.last_viewed_day = max(progress.last_viewed_day, day)
    if day == 7:
        flags["week1_v2_completed_on"] = today.isoformat()
    progress.flags = flags


def day_is_complete(progress: StoryProgress, day: int) -> bool:
    return bool(_flags(progress).get(f"v2_day_completed_{day}"))


def set_story_flag(progress: StoryProgress, key: str, value=True) -> None:
    flags = _flags(progress)
    flags[key] = value
    progress.flags = flags


def get_story_flag(progress: StoryProgress, key: str, default=None):
    return _flags(progress).get(key, default)


def current_story_objective(profile: UserProfile, progress: StoryProgress, today: date) -> str:
    day = story_day(progress, today)
    if day > 7:
        return "Продолжай текущую главу через 📖 История."
    if day_is_complete(progress, day):
        return "Сегодняшняя часть завершена. Следующая сцена откроется завтра."

    scene = get_scene(day)
    phase = week1_phase(progress)
    if phase == "intro":
        return f"Открой 📖 История — начинается «{scene['title']}»."
    if phase == "objective":
        status = objective_progress_text(profile, progress, day)
        return f"{scene.get('objective', 'Продолжи сюжетную задачу.')}\n{status}".strip()
    return "Вернись в 📖 История — Селин ждёт продолжения."


def _checkpoint_message(day: int) -> tuple[str, str, str]:
    return {
        1: ("selin", "surprised", "Селин: — Подожди. Печать опять среагировала. Кажется, получилось. Загляни в Историю."),
        2: ("tori", "curious", "Тори внезапно реагирует на твой голос. Кажется, связь изменилась. Загляни в Историю."),
        3: ("selin", "soft_smile", "Селин: — Я слышу тебя намного яснее. Похоже, второй уровень что-то изменил. Загляни в Историю."),
        4: ("selin", "smirk", "Селин: — Вот. Теперь механизм наконец перестал сопротивляться. Загляни в Историю."),
    }[day]


async def queue_story_checkpoint_if_ready(
    session: AsyncSession,
    profile: UserProfile,
    *,
    event_type: str,
    payload: dict | None = None,
) -> None:
    progress = await session.get(StoryProgress, profile.id)
    if not progress:
        return
    flags = _flags(progress)
    if not flags.get("week1_v2_initialized") or flags.get("v2_phase") != "objective":
        return

    day = int(flags.get("v2_active_day", 1))
    if day not in {1, 2, 3, 4} or flags.get(f"v2_objective_ready_{day}"):
        return

    ready = objective_ready(profile, progress, day)
    payload = payload or {}
    if day == 4:
        ready = (
            (event_type == "steps_update" and int(payload.get("steps", 0)) >= 5000)
            or event_type in {
                "manual_workout_complete",
                "monday_workout_complete",
                "thursday_first_complete",
                "thursday_bonus_complete",
            }
        )

    if not ready:
        return

    mark_objective_ready(progress, day)
    flags = _flags(progress)
    queue_key = f"v2_checkpoint_queued_{day}"
    if flags.get(queue_key):
        return

    flags[queue_key] = True
    progress.flags = flags
    character, emotion, text = _checkpoint_message(day)
    now = datetime.now(TZ)
    session.add(
        Notification(
            user_id=profile.id,
            kind="story_checkpoint",
            scheduled_at=now,
            dedupe_key=f"story-checkpoint:{profile.id}:{flags.get('week1_v2_run', 1)}:{day}:{now.date().isoformat()}",
            payload={"day": day, "character": character, "emotion": emotion, "text": text},
        )
    )
