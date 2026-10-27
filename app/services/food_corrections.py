"""Safe, reversible-in-diary food corrections; rewards and purchases are untouched.

No new database tables: current FoodNutritionLog, RhythmMealItem, DailyStat and
GameEvent are reused. All mutations happen after a user-owned review/confirmation.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AIImageAnalysis, DailyStat, FoodNutritionLog, GameEvent
from app.services.living_rhythm import RhythmMeal, RhythmMealItem, kind_at, now_local
from app.services.nutrition import daily_nutrition_totals

WINDOW_DAYS = 30
FIELDS = ('calories_kcal', 'protein_g', 'fat_g', 'carbs_g')
CATS = ('meal', 'snack', 'treat', 'drink')


def signature(log: FoodNutritionLog) -> str:
    """Reject stale confirmation after another edit, even from an older keyboard."""
    parts = (log.id, log.logged_on.isoformat(), log.category, log.description,
             log.portion_label, log.portion_grams, *(getattr(log, k) for k in FIELDS),
             log.nutrition_source, log.confidence)
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, default=str).encode()).hexdigest()[:12]


def allowed_date(day: date) -> bool:
    return now_local().date() - timedelta(days=WINDOW_DAYS) <= day <= now_local().date()


async def recent_logs(session: AsyncSession, user_id: int, limit: int = 20) -> list[FoodNutritionLog]:
    return list((await session.scalars(select(FoodNutritionLog).where(
        FoodNutritionLog.user_id == user_id,
        FoodNutritionLog.logged_on >= now_local().date() - timedelta(days=WINDOW_DAYS),
        FoodNutritionLog.logged_on <= now_local().date(),
    ).order_by(FoodNutritionLog.logged_on.desc(), FoodNutritionLog.id.desc()).limit(limit))).all())


async def checked_log(session: AsyncSession, user_id: int, log_id: int) -> FoodNutritionLog | None:
    # FOR UPDATE is honored by PostgreSQL. SQLite has a single-writer lock.
    log = await session.scalar(select(FoodNutritionLog).where(
        FoodNutritionLog.id == log_id, FoodNutritionLog.user_id == user_id).with_for_update())
    return log if log and allowed_date(log.logged_on) else None


def describe(log: FoodNutritionLog) -> str:
    n = f'{float(log.calories_kcal):g} ккал' if log.calories_kcal is not None else 'КБЖУ неизвестны'
    return f'#{log.id} · {log.logged_on:%d.%m} · {log.description[:70]} · {log.portion_label} · {n}'


def classify_drink(log: FoodNutritionLog, original_result: dict | None = None) -> str:
    if log.category != 'drink':
        return ''
    kind = (original_result or {}).get('drink_kind')
    if kind in {'plain', 'caloric', 'energy'}:
        return kind
    text = (log.description or '').casefold()
    if 'энергетик' in text or 'energy drink' in text:
        return 'energy'
    # For unlabelled drinks, use a *conservative* classification: unknown rather
    # than guessing that low calories means an automatically rewarded water log.
    return 'unknown'


async def _group_counts(session: AsyncSession, log: FoodNutritionLog, replace_category: str | None = None,
                        omit: bool = False) -> dict[str, int]:
    """Contribution of the affected meal before/after a deletion/category edit."""
    item = await session.scalar(select(RhythmMealItem).where(RhythmMealItem.food_log_id == log.id))
    if item:
        group = await session.get(RhythmMeal, item.meal_id)
        rows = list((await session.execute(select(RhythmMealItem, FoodNutritionLog).join(
            FoodNutritionLog, FoodNutritionLog.id == RhythmMealItem.food_log_id,
        ).where(RhythmMealItem.meal_id == item.meal_id))).all())
        categories = [
            ((replace_category or f.category) if f.id == log.id else f.category, float(i.portions or 1))
            for i, f in rows if not (omit and f.id == log.id)
        ]
        kind = group.kind if group is not None else 'unknown'
        # Some groups were merged; only the original recorded food counters are
        # adjusted, not previous rewards, story events or merged history.
    else:
        categories = [] if omit else [(replace_category or log.category, 1.0)]
        kind = 'unknown'
    meal = any(k == 'meal' for k, _ in categories) or (
        kind in {'breakfast', 'lunch', 'dinner'} and any(k != 'drink' for k, _ in categories)
    )
    return {
        'meals': int(meal),
        'snacks': int(not meal and any(k in {'snack', 'treat'} for k, _ in categories)),
        'drinks': sum(max(1, round(q)) for k, q in categories if k == 'drink'),
    }


async def _drink_kind_from_analysis(session: AsyncSession, log: FoodNutritionLog) -> str:
    if log.category != 'drink' or not log.analysis_id:
        return classify_drink(log)
    analysis = await session.get(AIImageAnalysis, log.analysis_id)
    return classify_drink(log, dict(analysis.result or {}) if analysis else None)


async def _apply_stat_delta(session: AsyncSession, log: FoodNutritionLog,
                            before: dict[str, int], after: dict[str, int],
                            old_kind: str, new_kind: str) -> None:
    stat = await session.scalar(select(DailyStat).where(
        DailyStat.user_id == log.user_id, DailyStat.stat_date == log.logged_on).with_for_update())
    if stat is None:
        return
    for key in ('meals', 'snacks', 'drinks'):
        setattr(stat, key, max(0, int(getattr(stat, key) or 0) + after[key] - before[key]))
    # Only adjust drink subcounters if the original kind is stored explicitly.
    # Unknown drinks are not silently classified as water or energy drinks.
    for old, sign in ((old_kind, -1), (new_kind, 1)):
        key = {'plain': 'plain_drinks', 'caloric': 'caloric_drinks', 'energy': 'energy_drinks'}.get(old)
        if key:
            setattr(stat, key, max(0, int(getattr(stat, key) or 0) + sign))
        # Water may have been entered separately, and can't be safely reversed
        # from FoodNutritionLog alone. Hydration/water and past rewards stay.


async def apply_correction(session: AsyncSession, user_id: int, log_id: int,
                           expected_signature: str, changes: dict | None = None,
                           *, delete: bool = False) -> tuple[FoodNutritionLog | None, dict]:
    """Atomic confirmed mutation; does not re-run rewards or challenge completion."""
    log = await checked_log(session, user_id, log_id)
    if not log or signature(log) != expected_signature:
        raise ValueError('Запись была изменена или удалена. Открой журнал заново.')
    if not delete and not changes:
        raise ValueError('Нет изменений')
    if changes:
        allowed = {'description', 'portion_grams', 'portion_label', 'category',
                   'nutrition_source', 'confidence', *FIELDS}
        if not set(changes) <= allowed:
            raise ValueError('Неверные поля изменения')
        if 'category' in changes and changes['category'] not in CATS:
            raise ValueError('Неверная категория')
        if 'description' in changes and not str(changes['description']).strip():
            raise ValueError('Название пустое')
        for k in FIELDS:
            if k in changes and changes[k] is not None:
                v = float(changes[k])
                if not 0 <= v <= (5000 if k == 'calories_kcal' else 1000):
                    raise ValueError('КБЖУ вне допустимого диапазона')
        if changes.get('portion_grams') is not None and not 0 < float(changes['portion_grams']) <= 5000:
            raise ValueError('Недопустимый вес')
    before = await _group_counts(session, log)
    old_kind = await _drink_kind_from_analysis(session, log)
    original = {k: getattr(log, k) for k in ('description', 'category', 'portion_grams', *FIELDS)}
    if delete:
        after = await _group_counts(session, log, omit=True)
    else:
        # A one-item meal corrected to a snack/drink is not still "breakfast".
        if changes.get('category') and changes['category'] != log.category:
            item = await session.scalar(select(RhythmMealItem).where(RhythmMealItem.food_log_id == log.id))
            if item:
                other = await session.scalar(select(RhythmMealItem.id).where(
                    RhythmMealItem.meal_id == item.meal_id,
                    RhythmMealItem.food_log_id != log.id).limit(1))
                if other is None:
                    group = await session.get(RhythmMeal, item.meal_id)
                    if group:
                        group.kind = (group.kind if changes['category'] == 'meal' and group.kind in {'breakfast', 'lunch', 'dinner'}
                                      else kind_at(now_local(), changes['category']))
                item.category = changes['category']
        after = await _group_counts(session, log, replace_category=changes.get('category'))
    # If category changes to drink, do not invent a drink type.
    new_kind = old_kind if not delete and (changes or {}).get('category', log.category) == 'drink' else ''
    if not delete:
        for key, value in changes.items():
            setattr(log, key, value)
        if 'description' in changes:
            log.description = str(log.description)[:300]
    else:
        # FK ON DELETE CASCADE removes RhythmMealItem. Empty RhythmMeal rows are
        # removed explicitly so breakfast reminders don't see phantom meals.
        item = await session.scalar(select(RhythmMealItem).where(RhythmMealItem.food_log_id == log.id))
        group_id = item.meal_id if item else None
        if item:
            await session.delete(item)
        await session.delete(log)
        await session.flush()
        if group_id is not None:
            count = await session.scalar(select(RhythmMealItem.id).where(
                RhythmMealItem.meal_id == group_id).limit(1))
            if count is None:
                group = await session.get(RhythmMeal, group_id)
                if group:
                    await session.delete(group)
    await _apply_stat_delta(session, log, before, after, old_kind, new_kind)
    session.add(GameEvent(user_id=user_id,
                          event_type='food_log_deleted' if delete else 'food_log_corrected',
                          xp_delta=0, coins_delta=0, atelier_dust_delta=0,
                          payload={'log_id': log_id, 'before': original,
                                   'after': None if delete else {k: getattr(log, k) for k in original}}))
    await session.flush()
    totals = await daily_nutrition_totals(session, user_id, log.logged_on)
    return (None if delete else log), totals

# Fast menu actions have no FoodNutritionLog; they are GameEvents only.
# Give them an independent, one-time, audited undo instead of pretending
# that an absent KБЖУ entry can be edited.
MANUAL_LABELS = {
    'meal_complete': 'Полноценная еда (ручная отметка)',
    'meal_manual_unrewarded': 'Полноценная еда (ручная отметка)',
    'snack': 'Перекус (ручная отметка)',
    'treat_logged': 'Вкусняшка (ручная отметка)',
    'drink': 'Напиток (ручная отметка)',
    'hydration': 'Вода/zero (ручная отметка)',
    'hydration_unrewarded': 'Вода/zero (ручная отметка)',
}


def manual_kind(event: GameEvent) -> str:
    payload = event.payload or {}
    kind = event.event_type
    if kind == 'treat_logged' and payload.get('source') != 'manual':
        return ''
    if kind in {'hydration', 'hydration_unrewarded'} and payload.get('source') not in {
        'manual_plain_drink', 'manual_water_legacy',
    }:
        return ''
    return kind if kind in MANUAL_LABELS else ''


async def recent_manual_marks(session: AsyncSession, user_id: int, limit: int = 50) -> list[GameEvent]:
    cutoff = now_local() - timedelta(days=WINDOW_DAYS)
    events = list((await session.scalars(select(GameEvent).where(
        GameEvent.user_id == user_id, GameEvent.occurred_at >= cutoff,
        GameEvent.event_type.in_([*MANUAL_LABELS, 'manual_food_undone']),
    ).order_by(GameEvent.id.desc()).limit(250))).all())
    undone = {int(e.payload['reversed_event_id']) for e in events if e.event_type == 'manual_food_undone'
              and (e.payload or {}).get('reversed_event_id') is not None}
    return [e for e in events if manual_kind(e) and e.id not in undone][:limit]


def manual_description(event: GameEvent) -> str:
    return f'#{event.id} · {MANUAL_LABELS[manual_kind(event)]}'


async def undo_manual_mark(session: AsyncSession, user_id: int, event_id: int) -> tuple[str, date]:
    event = await session.scalar(select(GameEvent).where(
        GameEvent.id == event_id, GameEvent.user_id == user_id).with_for_update())
    if event is None or not manual_kind(event):
        raise ValueError('Не найдена ручная отметка')
    since = now_local() - timedelta(days=WINDOW_DAYS)
    moment = event.occurred_at
    if moment is None:
        raise ValueError('Дата отметки неизвестна')
    # SQLite may deserialize old timestamps as naive; do not assume a different
    # timezone for the date from the date used when storing the quick mark.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if not since <= moment <= now_local() + timedelta(minutes=1):
        raise ValueError('Можно отменять отметки за последние 30 дней')
    reversal_events = list((await session.scalars(select(GameEvent).where(
        GameEvent.user_id == user_id, GameEvent.event_type == 'manual_food_undone',
    ))).all())
    if any((e.payload or {}).get('reversed_event_id') == event_id for e in reversal_events):
        raise ValueError('Эта отметка уже отменена')
    day = moment.astimezone(now_local().tzinfo).date()
    stat = await session.scalar(select(DailyStat).where(
        DailyStat.user_id == user_id, DailyStat.stat_date == day).with_for_update())
    if stat is None:
        raise ValueError('Не найден дневной счётчик этой отметки')
    kind = manual_kind(event)
    deltas = {
        'meal_complete': ('meals',), 'meal_manual_unrewarded': ('meals',), 'snack': ('snacks',),
        'treat_logged': ('snacks',),
        'drink': ('drinks', 'energy_drinks' if (event.payload or {}).get('kind') == 'energy' else 'caloric_drinks'),
        'hydration': ('drinks', 'plain_drinks', 'water'),
        'hydration_unrewarded': ('drinks', 'plain_drinks', 'water'),
    }[kind]
    for field in deltas:
        setattr(stat, field, max(0, int(getattr(stat, field) or 0) - 1))
    session.add(GameEvent(user_id=user_id, event_type='manual_food_undone',
                          xp_delta=0, coins_delta=0, atelier_dust_delta=0,
                          payload={'reversed_event_id': event_id, 'original_type': kind}))
    await session.flush()
    return MANUAL_LABELS[kind], day
