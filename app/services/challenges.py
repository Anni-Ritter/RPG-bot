from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import DailyChallengePlan, DailyStat, FoodNutritionLog, UserProfile
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


def fallback_options(weekday: int) -> list[dict]:
    movement = {
        "code": "workout" if weekday in {0, 3} else "steps",
        "title": "День не мимо",
        "description": "Закрой тренировку сегодня." if weekday in {0, 3} else "Набери 7 000 шагов за день.",
        "target": 20 if weekday in {0, 3} else 7000,
        "why": "Пусть сегодня будет хотя бы одна понятная победа.",
    }
    return [
        movement,
        {
            "code": "meals",
            "title": "Нормальная еда",
            "description": "Отметь 3 полноценных приёма пищи.",
            "target": 3,
            "why": "Меньше хаотичных перекусов — больше нормальной еды.",
        },
        {
            "code": "treat_limit",
            "title": "Вкусняшка по плану",
            "description": "Оставь сегодня максимум одну вкусняшку.",
            "target": 1,
            "why": "Не запрет, а выбранная граница на один день.",
        },
    ]


async def get_plan(session: AsyncSession, user_id: int, day: date) -> DailyChallengePlan | None:
    return await session.scalar(
        select(DailyChallengePlan).where(
            DailyChallengePlan.user_id == user_id,
            DailyChallengePlan.challenge_date == day,
        )
    )


async def ensure_plan(session: AsyncSession, profile: UserProfile, day: date | None = None) -> DailyChallengePlan:
    day = day or now_local().date()
    existing = await get_plan(session, profile.id, day)
    if existing:
        return existing

    options: list[dict] = []
    if ai_enabled() and await reserve_ai_call(session, profile.id, "challenge"):
        context = await build_ai_context(session, profile)
        coach_rules = await get_coach_rules(session, profile.id)
        # Avoid holding a SQLite write lock while waiting for the network.
        await session.commit()
        try:
            generated = await generate_daily_challenges(context=context, coach_rules=coach_rules)
            options = sanitize_options(generated.get("options"))
        except Exception as exc:
            print("AI daily challenge generation error:", repr(exc))

    if len(options) < 3:
        for item in fallback_options(day.weekday()):
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
        selected={},
        status="choosing",
        reward_xp=CHALLENGE_REWARD_XP,
        reward_coins=CHALLENGE_REWARD_COINS,
    )
    session.add(plan)
    await session.flush()
    return plan


async def select_option(session: AsyncSession, plan: DailyChallengePlan, index: int) -> bool:
    if plan.status != "choosing":
        return False
    options = list(plan.options or [])
    if index < 0 or index >= len(options):
        return False
    plan.selected = dict(options[index])
    plan.status = "active"
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


async def challenge_value(session: AsyncSession, profile: UserProfile, plan: DailyChallengePlan) -> int:
    selected = dict(plan.selected or {})
    code = selected.get("code")
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
    return 0


def is_limit_challenge(code: str) -> bool:
    return code in {"treat_limit", "energy_limit"}


def progress_text(code: str, current: int, target: int, *, final: bool = False) -> str:
    if code == "steps":
        return f"{current:,} / {target:,} шагов".replace(",", " ")
    if code == "workout":
        return f"{current} / {target} мин тренировки"
    if code == "water":
        return f"{current} / {target} отметок воды"
    if code == "meals":
        return f"{current} / {target} полноценных приёмов пищи"
    if code == "food_logs":
        return f"{current} / {target} записей еды"
    if code == "protein_meals":
        return f"{current} / {target} белковых приёмов пищи"
    if code == "treat_limit":
        suffix = " · итог вечером" if not final else ""
        return f"сейчас {current}, максимум {target} вкусняшек{suffix}"
    if code == "energy_limit":
        suffix = " · итог вечером" if not final else ""
        return f"сейчас {current}, максимум {target} энергетик{suffix}"
    return str(current)


async def evaluate_plan(
    session: AsyncSession,
    profile: UserProfile,
    plan: DailyChallengePlan,
    *,
    final: bool = False,
) -> tuple[str, int, bool]:
    if plan.status in {"completed", "failed"}:
        current = await challenge_value(session, profile, plan) if plan.selected else 0
        return plan.status, current, False
    if plan.status != "active" or not plan.selected:
        return plan.status, 0, False

    selected = dict(plan.selected)
    code = str(selected.get("code") or "")
    target = int(selected.get("target") or 0)
    current = await challenge_value(session, profile, plan)

    if is_limit_challenge(code):
        # Do not award a "limit" challenge at noon and then invalidate it later.
        ready_to_close = final or now_local().hour >= 20
        if not ready_to_close:
            return "active", current, False
        completed = current <= target
    else:
        completed = current >= target

    if completed:
        plan.status = "completed"
        plan.completed_at = now_local()
        await apply_reward(
            session,
            profile,
            event_type="daily_challenge_complete",
            xp=plan.reward_xp,
            coins=plan.reward_coins,
            payload={"challenge_date": plan.challenge_date.isoformat(), **selected},
        )
        # Flush first so the just-completed row is visible to the aggregate query.
        await session.flush()
        completed_count = await session.scalar(
            select(func.count(DailyChallengePlan.id)).where(
                DailyChallengePlan.user_id == profile.id,
                DailyChallengePlan.status == "completed",
            )
        )
        chest = bool(completed_count and int(completed_count) % 3 == 0)
        if chest:
            profile.trial_chests += 1
        return "completed", current, chest

    if final:
        plan.status = "failed"
        return "failed", current, False
    return "active", current, False


def challenge_summary(plan: DailyChallengePlan, current: int | None = None, *, final: bool = False) -> str:
    if not plan.selected:
        return "Челлендж ещё не выбран."
    selected = dict(plan.selected)
    text = f"{selected.get('title', 'Челлендж дня')}: {selected.get('description', '')}".strip()
    if current is not None:
        text += "\n" + progress_text(str(selected.get("code") or ""), current, int(selected.get("target") or 0), final=final)
    return text
