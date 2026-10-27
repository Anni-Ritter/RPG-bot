from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import DailyChallengePlan, DailyStat, FoodNutritionLog, GameEvent, UserProfile
from app.services.ai_engine import ai_enabled, generate_daily_challenges
from app.services.ai_features import build_ai_context, daily_treat_count, get_coach_rules, reserve_ai_call
from app.services.nutrition import daily_protein_meal_count
from app.services.rewards import apply_reward, get_or_create_daily
from app.services.self_workouts import daily_workout_minutes

TZ = ZoneInfo(settings.timezone)

CHALLENGE_REWARD_XP = 60
CHALLENGE_REWARD_COINS = 15

_TARGETS = {
    "steps": [5000, 7000, 9000, 12000],
    "water": [2, 3, 4],
    "meals": [2, 3],
    "workout": [10, 20, 30, 45],
    "food_logs": [2, 3, 4],
    "treat_limit": [1, 2],
    "energy_limit": [1],
    "protein_meals": [1, 2, 3],
    "tori_bond": [1, 2, 3],
    "tori_interactions": [1, 2, 3],
    "willpower_actions": [1, 2],
    "quests": [1, 2],
}


def now_local() -> datetime:
    return datetime.now(TZ)


def _nearest_allowed(code: str, target: int) -> int:
    values = _TARGETS.get(code) or [1]
    return min(values, key=lambda x: abs(x - int(target or values[0])))


def sanitize_options(raw: list[dict] | None) -> list[dict]:
    options: list[dict] = []
    seen: set[str] = set()
    for item in list(raw or []):
        code = str(item.get("code") or "").strip()
        if code not in _TARGETS or code in seen:
            continue
        seen.add(code)
        target = _nearest_allowed(code, int(item.get("target") or _TARGETS[code][0]))
        title = str(item.get("title") or "Челлендж дня").strip()[:120]
        description = str(item.get("description") or "").strip()[:320]
        why = str(item.get("why") or "").strip()[:320]
        options.append({
            "code": code,
            "title": title,
            "description": description,
            "target": target,
            "why": why,
        })
        if len(options) == 3:
            break
    return options


def fallback_options(day: date) -> list[dict]:
    """Varied local fallback when AI is unavailable. Rotates by date instead of always steps/meals/treats."""
    pools = [
        [
            {"code": "steps", "title": "Разведка района", "description": "Набери 7 000 шагов за день.", "target": 7000, "why": "Нормальный марш без требования идти всё сразу."},
            {"code": "tori_interactions", "title": "Лисий инструктаж", "description": "Сделай 2 разных взаимодействия с Тори.", "target": 2, "why": "Иногда подготовка к приключениям — это ещё и не игнорировать фамильяра."},
            {"code": "treat_limit", "title": "Один трофей", "description": "Оставь сегодня максимум одну вкусняшку.", "target": 1, "why": "Не запрет, а выбранная граница на один день."},
        ],
        [
            {"code": "workout", "title": "Проверка снаряжения", "description": "Набери 20 минут любой тренировки.", "target": 20, "why": "Короткая тренировка тоже считается подготовкой."},
            {"code": "food_logs", "title": "Журнал припасов", "description": "Запиши через фото 2 приёма пищи или продукта.", "target": 2, "why": "Чем лучше записи, тем полезнее советы Селин."},
            {"code": "quests", "title": "Одно дело до конца", "description": "Закрой сегодня 1 квест.", "target": 1, "why": "Небольшая законченная задача лучше пяти начатых."},
        ],
        [
            {"code": "tori_bond", "title": "Связь с проводником", "description": "Получи сегодня +2 связи с Тори.", "target": 2, "why": "Связь можно набрать общением, гидратацией и некоторыми событиями."},
            {"code": "meals", "title": "Три остановки", "description": "Отметь 3 полноценных приёма пищи.", "target": 3, "why": "Меньше хаотичных перекусов, больше нормальной еды."},
            {"code": "willpower_actions", "title": "Проверка Воли", "description": "Один раз поймай импульс или используй паузу искушения.", "target": 1, "why": "Не запрещать себе, а хотя бы один раз заметить автоматическое решение."},
        ],
        [
            {"code": "steps", "title": "Дальний маршрут", "description": "Набери 9 000 шагов за день.", "target": 9000, "why": "Сегодня можно сделать упор на движение."},
            {"code": "protein_meals", "title": "Нормальный паёк", "description": "Собери 2 полноценных приёма пищи примерно с 20+ г белка.", "target": 2, "why": "Белок — основа, но не единственное содержимое тарелки."},
            {"code": "water", "title": "Фляга рядом", "description": "Отметь 3 порции гидратации.", "target": 3, "why": "Вода, zero и почти некалорийные напитки тоже подходят."},
        ],
    ]
    return pools[day.toordinal() % len(pools)]


async def get_plan(session: AsyncSession, user_id: int, day: date) -> DailyChallengePlan | None:
    return await session.scalar(
        select(DailyChallengePlan).where(
            DailyChallengePlan.user_id == user_id,
            DailyChallengePlan.challenge_date == day,
        )
    )


async def _recent_challenge_history(session: AsyncSession, user_id: int, day: date) -> str:
    rows = list((await session.scalars(
        select(DailyChallengePlan)
        .where(
            DailyChallengePlan.user_id == user_id,
            DailyChallengePlan.challenge_date < day,
            DailyChallengePlan.challenge_date >= day - timedelta(days=7),
        )
        .order_by(DailyChallengePlan.challenge_date.desc())
    )).all())
    lines = []
    for row in rows:
        opts = list(row.options or [])
        if not opts:
            continue
        rendered = ", ".join(f"{o.get('code')} — {o.get('title')}" for o in opts[:3])
        lines.append(f"{row.challenge_date.isoformat()}: {rendered}")
    return "\n".join(lines)


async def _count_events(session: AsyncSession, user_id: int, day: date, event_types: list[str]) -> int:
    start = datetime.combine(day, time.min, tzinfo=TZ)
    end = datetime.combine(day, time.max, tzinfo=TZ)
    value = await session.scalar(
        select(func.count(GameEvent.id)).where(
            GameEvent.user_id == user_id,
            GameEvent.event_type.in_(event_types),
            GameEvent.occurred_at >= start,
            GameEvent.occurred_at <= end,
        )
    )
    return int(value or 0)


async def _bond_gained_today(session: AsyncSession, user_id: int, day: date) -> int:
    start = datetime.combine(day, time.min, tzinfo=TZ)
    end = datetime.combine(day, time.max, tzinfo=TZ)
    rows = list((await session.scalars(
        select(GameEvent).where(
            GameEvent.user_id == user_id,
            GameEvent.occurred_at >= start,
            GameEvent.occurred_at <= end,
        )
    )).all())
    return sum(max(0, int((row.payload or {}).get("tori_bond_delta") or 0)) for row in rows)


async def ensure_plan(session: AsyncSession, profile: UserProfile, day: date | None = None) -> DailyChallengePlan:
    day = day or now_local().date()
    existing = await get_plan(session, profile.id, day)
    if existing:
        return existing

    options: list[dict] = []
    if ai_enabled() and await reserve_ai_call(session, profile.id, "challenge"):
        context = await build_ai_context(session, profile)
        coach_rules = await get_coach_rules(session, profile.id)
        recent_history = await _recent_challenge_history(session, profile.id, day)
        # Avoid holding a SQLite write lock while waiting for the network.
        await session.commit()
        try:
            generated = await generate_daily_challenges(context=context, coach_rules=coach_rules, recent_history=recent_history)
            options = sanitize_options(generated.get("options"))
        except Exception as exc:
            print("AI daily challenge generation error:", repr(exc))

    if len(options) < 3:
        for item in fallback_options(day):
            if item["code"] not in {x["code"] for x in options}:
                options.append(item)
            if len(options) == 3:
                break

    # Re-check after network call in case another handler created it.
    existing = await get_plan(session, profile.id, day)
    if existing:
        return existing

    plan = DailyChallengePlan(
        user_id=profile.id,
        challenge_date=day,
        options=options[:3],
        selected={"accepted": [], "completed": [], "failed": []},
        status="choosing",
        reward_xp=CHALLENGE_REWARD_XP,
        reward_coins=CHALLENGE_REWARD_COINS,
    )
    session.add(plan)
    await session.flush()
    return plan


def challenge_state(plan: DailyChallengePlan) -> dict[str, list[int]]:
    """Return the V12 multi-challenge state, migrating the old single selection in memory."""
    raw = dict(plan.selected or {})
    options = list(plan.options or [])

    if any(key in raw for key in ("accepted", "completed", "failed")):
        def clean(name: str) -> list[int]:
            values = []
            for value in list(raw.get(name) or []):
                try:
                    idx = int(value)
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < len(options) and idx not in values:
                    values.append(idx)
            return values

        accepted = clean("accepted")
        completed = [i for i in clean("completed") if i in accepted]
        failed = [i for i in clean("failed") if i in accepted and i not in completed]
        return {"accepted": accepted, "completed": completed, "failed": failed}

    # Legacy V9-V11 format: selected contained one challenge object.
    if raw.get("code"):
        match = next(
            (
                i for i, option in enumerate(options)
                if option.get("code") == raw.get("code")
                and (not raw.get("title") or option.get("title") == raw.get("title"))
            ),
            None,
        )
        if match is None:
            match = next((i for i, option in enumerate(options) if option.get("code") == raw.get("code")), None)
        if match is not None:
            completed = [match] if plan.status == "completed" else []
            failed = [match] if plan.status == "failed" else []
            return {"accepted": [match], "completed": completed, "failed": failed}

    return {"accepted": [], "completed": [], "failed": []}


def _store_state(plan: DailyChallengePlan, state: dict[str, list[int]]) -> None:
    plan.selected = {
        "accepted": sorted(set(state.get("accepted") or [])),
        "completed": sorted(set(state.get("completed") or [])),
        "failed": sorted(set(state.get("failed") or [])),
    }


def active_indices(plan: DailyChallengePlan) -> list[int]:
    state = challenge_state(plan)
    terminal = set(state["completed"]) | set(state["failed"])
    return [i for i in state["accepted"] if i not in terminal]


def available_indices(plan: DailyChallengePlan) -> list[int]:
    accepted = set(challenge_state(plan)["accepted"])
    return [i for i in range(len(list(plan.options or []))) if i not in accepted]


def select_option(session: AsyncSession, plan: DailyChallengePlan, index: int) -> bool:
    options = list(plan.options or [])
    if index < 0 or index >= len(options):
        return False
    state = challenge_state(plan)
    if index in state["accepted"]:
        return False
    state["accepted"].append(index)
    _store_state(plan, state)
    plan.status = "active"
    if plan.selected_at is None:
        plan.selected_at = now_local()
    return True


async def _food_log_count(session: AsyncSession, user_id: int, day: date) -> int:
    count = await session.scalar(
        select(func.count(FoodNutritionLog.id)).where(
            FoodNutritionLog.user_id == user_id,
            FoodNutritionLog.logged_on == day,
        )
    )
    return int(count or 0)


async def challenge_value_for_option(
    session: AsyncSession,
    profile: UserProfile,
    plan: DailyChallengePlan,
    option: dict,
) -> int:
    code = option.get("code")
    day = plan.challenge_date
    stat = await get_or_create_daily(session, profile.id, day)
    if code == "steps":
        return int(stat.steps or 0)
    if code == "water":
        return int(stat.water or 0)
    if code == "meals":
        return int(stat.meals or 0)
    if code == "workout":
        return await daily_workout_minutes(session, profile.id, day)
    if code == "food_logs":
        return await _food_log_count(session, profile.id, day)
    if code == "treat_limit":
        return await daily_treat_count(session, profile.id, day)
    if code == "energy_limit":
        return int(stat.energy_drinks or 0)
    if code == "protein_meals":
        return await daily_protein_meal_count(session, profile.id, day)
    if code == "tori_bond":
        return await _bond_gained_today(session, profile.id, day)
    if code == "tori_interactions":
        return await _count_events(session, profile.id, day, ["tori_interaction_bond"])
    if code == "willpower_actions":
        return await _count_events(session, profile.id, day, [
            "temptation_impulse_caught", "temptation_pause", "temptation_after_pause_declined",
        ])
    if code == "quests":
        return await _count_events(session, profile.id, day, ["custom_quest_complete"])
    return 0


async def challenge_value(session: AsyncSession, profile: UserProfile, plan: DailyChallengePlan) -> int:
    """Backward-compatible helper: value of the first active/accepted challenge."""
    state = challenge_state(plan)
    candidates = active_indices(plan) or state["accepted"]
    if not candidates:
        return 0
    options = list(plan.options or [])
    return await challenge_value_for_option(session, profile, plan, options[candidates[0]])


def is_limit_challenge(code: str) -> bool:
    return code in {"treat_limit", "energy_limit"}


def progress_text(code: str, current: int, target: int, *, final: bool = False) -> str:
    if code == "steps":
        return f"{current:,} / {target:,} шагов".replace(",", " ")
    if code == "workout":
        return f"{current} / {target} мин тренировки"
    if code == "water":
        return f"{current} / {target} отметок гидратации"
    if code == "meals":
        return f"{current} / {target} полноценных приёмов пищи"
    if code == "food_logs":
        return f"{current} / {target} записей еды"
    if code == "protein_meals":
        return f"{current} / {target} белковых приёмов пищи"
    if code == "tori_bond":
        return f"+{current} / +{target} связи с Тори сегодня"
    if code == "tori_interactions":
        return f"{current} / {target} разных взаимодействий с Тори"
    if code == "willpower_actions":
        return f"{current} / {target} осознанных действий Воли"
    if code == "quests":
        return f"{current} / {target} закрытых квестов"
    if code == "treat_limit":
        suffix = " · итог вечером" if not final else ""
        return f"сейчас {current}, максимум {target} вкусняшек{suffix}"
    if code == "energy_limit":
        suffix = " · итог вечером" if not final else ""
        return f"сейчас {current}, максимум {target} энергетик{suffix}"
    return str(current)


async def _award_completed_option(
    session: AsyncSession,
    profile: UserProfile,
    plan: DailyChallengePlan,
    index: int,
    option: dict,
) -> int:
    await apply_reward(
        session,
        profile,
        event_type="daily_challenge_complete",
        xp=plan.reward_xp,
        coins=plan.reward_coins,
        payload={
            "challenge_date": plan.challenge_date.isoformat(),
            "challenge_index": index,
            **option,
        },
    )
    await session.flush()
    completed_count = await session.scalar(
        select(func.count(GameEvent.id)).where(
            GameEvent.user_id == profile.id,
            GameEvent.event_type == "daily_challenge_complete",
        )
    )
    if completed_count and int(completed_count) % 3 == 0:
        profile.trial_chests += 1
        return 1
    return 0


async def evaluate_plan(
    session: AsyncSession,
    profile: UserProfile,
    plan: DailyChallengePlan,
    *,
    final: bool = False,
) -> tuple[str, dict[int, int], int]:
    """Evaluate every accepted challenge. Returns (plan_status, values_by_index, chests_awarded)."""
    options = list(plan.options or [])
    state = challenge_state(plan)
    values: dict[int, int] = {}
    chests = 0

    for index in list(state["accepted"]):
        if index < 0 or index >= len(options):
            continue
        option = dict(options[index])
        current = await challenge_value_for_option(session, profile, plan, option)
        values[index] = current

        if index in state["completed"] or index in state["failed"]:
            continue

        code = str(option.get("code") or "")
        target = int(option.get("target") or 0)
        if is_limit_challenge(code):
            ready_to_close = final
            if not ready_to_close:
                continue
            completed = current <= target
        else:
            completed = current >= target

        if completed:
            state["completed"].append(index)
            chests += await _award_completed_option(session, profile, plan, index, option)
        elif final:
            state["failed"].append(index)

    _store_state(plan, state)

    active = active_indices(plan)
    available = available_indices(plan)
    if active:
        plan.status = "active"
    elif available and not final:
        plan.status = "choosing"
    elif state["failed"]:
        plan.status = "failed"
    elif state["completed"]:
        plan.status = "completed"
        plan.completed_at = now_local()
    else:
        plan.status = "choosing"

    return plan.status, values, chests


def challenge_summary(
    plan: DailyChallengePlan,
    values: dict[int, int] | None = None,
    *,
    final: bool = False,
) -> str:
    options = list(plan.options or [])
    state = challenge_state(plan)
    values = values or {}
    lines: list[str] = []
    for index, option in enumerate(options[:3]):
        if index in state["completed"]:
            icon = "✅"
        elif index in state["failed"]:
            icon = "❌"
        elif index in state["accepted"]:
            icon = "🎯"
        else:
            icon = "▫️"
        line = f"{icon} {index + 1}. {option.get('title', 'Челлендж')}: {option.get('description', '')}".strip()
        if index in state["accepted"] and index in values:
            line += "\n   " + progress_text(
                str(option.get("code") or ""),
                values[index],
                int(option.get("target") or 0),
                final=final,
            )
        elif final and index not in state["accepted"]:
            line += " · не брала"
        lines.append(line)
    return "\n".join(lines) if lines else "Челленджей на сегодня нет."


async def challenge_dashboard_line(
    session: AsyncSession,
    profile: UserProfile,
    plan: DailyChallengePlan | None,
) -> str:
    if plan is None:
        return "🎯 Челленджи: ещё не выбраны"
    state = challenge_state(plan)
    active = active_indices(plan)
    if active:
        options = list(plan.options or [])
        parts = []
        for index in active[:3]:
            option = options[index]
            current = await challenge_value_for_option(session, profile, plan, option)
            parts.append(
                f"{option.get('title', 'Челлендж')}: "
                + progress_text(str(option.get("code") or ""), current, int(option.get("target") or 0))
            )
        done = len(state["completed"])
        suffix = f" · закрыто {done}" if done else ""
        return "🎯 " + " | ".join(parts) + suffix
    if state["completed"]:
        return f"🎯 Челленджи: закрыто {len(state['completed'])} · можно взять ещё {len(available_indices(plan))}"
    return "🎯 Челленджи: выбери один, два или все три"

# SELIN_ACTIVITY_BREAK_20261027_BEGIN
# Respect paused activity when creating or retrieving a daily challenge plan.
_activity_break_previous_ensure_plan = ensure_plan

async def ensure_plan(session, profile, day=None):
    plan = await _activity_break_previous_ensure_plan(session, profile, day)
    from app.services.activity_breaks import adapt_challenge_plan, is_paused, now_local
    if await is_paused(session, profile.id, day or now_local().date()):
        adapt_challenge_plan(plan)
    return plan
# SELIN_ACTIVITY_BREAK_20261027_END
