"""Persistent, opt-in-confirmed meal groups and rhythm observations for Selin.

All SQL writes happen in Python services; AI never receives a database session.
New tables are registered by importing this module before init_db().
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Boolean, Float, UniqueConstraint, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.db import Base
from app.models import DailyStat, FoodNutritionLog, UserProfile

TZ = ZoneInfo(settings.timezone)
KINDS = frozenset({'breakfast', 'lunch', 'dinner', 'snack', 'unknown'})
MODES = frozenset({'calm', 'normal', 'caring'})


def now_local() -> datetime:
    return datetime.now(TZ)


def aware(moment: datetime) -> datetime:
    return moment.replace(tzinfo=TZ) if moment.tzinfo is None else moment.astimezone(TZ)


def kind_at(moment: datetime, category: str = 'meal') -> str:
    if category in {'snack', 'treat'}:
        return 'snack'
    if category == 'drink':
        return 'unknown'  # Coffee by itself isn't proof of a full breakfast.
    hour = moment.hour
    if 5 <= hour < 11:
        return 'breakfast'
    if 11 <= hour < 16:
        return 'lunch'
    if 16 <= hour < 23:
        return 'dinner'
    return 'unknown'


class RhythmSettings(Base):
    __tablename__ = 'selin_rhythm_settings'
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id', ondelete='CASCADE'), primary_key=True)
    mode: Mapped[str] = mapped_column(String(20), default='normal')
    last_feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    feedback_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    feedback_count: Mapped[int] = mapped_column(Integer, default=0)
    last_breakfast_nudge: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_treat_question: Mapped[date | None] = mapped_column(Date, nullable=True)


class RhythmMeal(Base):
    __tablename__ = 'selin_rhythm_meals'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id', ondelete='CASCADE'), index=True)
    eaten_on: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(20), default='unknown')
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_item_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    feedback_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    merge_prompt_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    merged_into_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RhythmMealItem(Base):
    __tablename__ = 'selin_rhythm_meal_items'
    __table_args__ = (UniqueConstraint('food_log_id', name='uq_rhythm_food_log'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meal_id: Mapped[int] = mapped_column(ForeignKey('selin_rhythm_meals.id', ondelete='CASCADE'), index=True)
    food_log_id: Mapped[int] = mapped_column(ForeignKey('food_nutrition_logs.id', ondelete='CASCADE'), index=True)
    recipe_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category: Mapped[str] = mapped_column(String(20))
    portions: Mapped[float] = mapped_column(Float, default=1.0)


class RhythmProposal(Base):
    __tablename__ = 'selin_rhythm_proposals'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id', ondelete='CASCADE'), index=True)
    kind: Mapped[str] = mapped_column(String(20), default='unknown')
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_local)


class RhythmEvent(Base):
    __tablename__ = 'selin_rhythm_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id', ondelete='CASCADE'), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    detail: Mapped[str] = mapped_column(String(120), default='')
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_local, index=True)


async def get_settings(session: AsyncSession, user_id: int) -> RhythmSettings:
    row = await session.get(RhythmSettings, user_id)
    if row is None:
        row = RhythmSettings(user_id=user_id, mode='normal', feedback_count=0)
        session.add(row)
        await session.flush()
    return row


async def observe_entry(session: AsyncSession, log: FoodNutritionLog) -> RhythmMeal:
    """Called only inside an existing confirmed food transaction (idempotent)."""
    existing = await session.scalar(select(RhythmMealItem).where(RhythmMealItem.food_log_id == log.id))
    if existing:
        return await session.get(RhythmMeal, existing.meal_id)
    stamp = now_local()
    group_id = session.info.get('lr_group_id')
    meal = await session.get(RhythmMeal, group_id) if group_id is not None else None
    if meal and (meal.user_id != log.user_id or meal.eaten_on != log.logged_on or meal.merged_into_id):
        raise ValueError('Meal group cannot include another user or date')
    if meal is None:
        meal = RhythmMeal(user_id=log.user_id, eaten_on=log.logged_on,
                          kind=kind_at(stamp, log.category), started_at=stamp, last_item_at=stamp)
        session.add(meal)
        await session.flush()
    else:
        meal.last_item_at = stamp
    portions = float(session.info.get('lr_portions', 1) or 1)
    session.add(RhythmMealItem(meal_id=meal.id, food_log_id=log.id,
                               category=log.category,
                               recipe_id=session.info.get('lr_recipe_id'), portions=portions))
    await session.flush()
    return meal


async def create_group(session: AsyncSession, user_id: int, kind: str, *, stamp: datetime | None = None) -> RhythmMeal:
    stamp = stamp or now_local()
    meal = RhythmMeal(user_id=user_id, eaten_on=stamp.date(),
                      kind=kind if kind in KINDS else 'unknown',
                      started_at=stamp, last_item_at=stamp)
    session.add(meal)
    await session.flush()
    return meal


async def group_items(session: AsyncSession, group_id: int) -> list[tuple[RhythmMealItem, FoodNutritionLog]]:
    q = select(RhythmMealItem, FoodNutritionLog).join(
        FoodNutritionLog, FoodNutritionLog.id == RhythmMealItem.food_log_id
    ).where(RhythmMealItem.meal_id == group_id).order_by(RhythmMealItem.id)
    return list((await session.execute(q)).all())


async def group_summary(session: AsyncSession, meal: RhythmMeal) -> str:
    items = await group_items(session, meal.id)
    label = {'breakfast': 'завтрак', 'lunch': 'обед', 'dinner': 'ужин', 'snack': 'перекус'}.get(meal.kind, 'приём пищи')
    names = [str(food.description or 'без названия')[:70] for _, food in items]
    return f'{label}: ' + ', '.join(names[:5])


async def merge_group(session: AsyncSession, user_id: int, earlier_id: int, later_id: int) -> bool:
    earlier = await session.get(RhythmMeal, earlier_id)
    later = await session.get(RhythmMeal, later_id)
    if not earlier or not later or earlier.id == later.id:
        return False
    if earlier.user_id != user_id or later.user_id != user_id or earlier.eaten_on != later.eaten_on:
        return False
    if earlier.merged_into_id or later.merged_into_id:
        return False
    if abs((aware(later.started_at) - aware(earlier.last_item_at)).total_seconds()) > 45 * 60:
        return False
    items = await group_items(session, later.id)
    prev = await group_items(session, earlier.id)
    # Only merge existing logs where the second is drinks or snacks, not another
    # independently-rewarded full meal. This avoids retroactively changing XP.
    if not items or any(item.category == 'meal' for item, _ in items) and any(item.category == 'meal' for item, _ in prev):
        return False
    for item, _ in items:
        item.meal_id = earlier.id
    earlier.last_item_at = max(aware(earlier.last_item_at), aware(later.last_item_at))
    later.merged_into_id = earlier.id
    later.feedback_sent_at = now_local()
    return True


async def append_event(session: AsyncSession, user_id: int, kind: str, detail: str = '') -> None:
    if kind not in {'craving', 'choice', 'travel', 'schedule_change', 'not_hungry', 'question'}:
        return
    session.add(RhythmEvent(user_id=user_id, kind=kind, detail=str(detail)[:120]))


async def create_proposal(session: AsyncSession, user_id: int, kind: str, items: list[dict]) -> RhythmProposal:
    if not 1 <= len(items) <= 6:
        raise ValueError('Meal requires 1–6 reviewed items')
    row = RhythmProposal(user_id=user_id, kind=kind if kind in KINDS else 'unknown', items=items)
    session.add(row)
    await session.flush()
    return row


async def record_confirmed_group(session: AsyncSession, profile: UserProfile, proposal: RhythmProposal) -> tuple[RhythmMeal, list[str]]:
    """Exactly one meal reward for a multi-item meal; every item keeps its own KБЖУ.

    Caller owns the transaction, validates status, and commits once. No AI calls here.
    """
    from app.services.nutrition import normalize_food_result, record_food_nutrition
    from app.services.rewards import apply_reward, get_or_create_daily
    from app.services.hydration import record_hydration

    if proposal.status != 'pending' or proposal.user_id != profile.id:
        raise ValueError('Proposal already processed or not yours')
    if aware(proposal.created_at) + timedelta(hours=2) < now_local():
        raise ValueError('Proposal expired')
    entries = list(proposal.items or [])
    if not entries or len(entries) > 6:
        raise ValueError('Invalid items')
    day = now_local().date()
    stat = await get_or_create_daily(session, profile.id, day)
    meal = await create_group(session, profile.id, proposal.kind)
    lines = []
    categories = []
    total_treat_units = 0
    session.info['lr_group_id'] = meal.id
    try:
        for entry in entries:
            category = str(entry.get('category') or 'unknown')
            if category not in {'meal', 'snack', 'treat', 'drink'}:
                raise ValueError('Unconfirmed category')
            qty = float(entry.get('quantity') or 1)
            if not 0.1 <= qty <= 5:
                raise ValueError('Portion quantity must be 0.1–5')
            recipe_id = entry.get('recipe_id')
            recipe = None
            if recipe_id is not None:
                from app.models import SavedRecipe, RecipeBatch
                recipe = await session.get(SavedRecipe, int(recipe_id))
                if not recipe or recipe.user_id != profile.id:
                    raise ValueError('Recipe is no longer available')
                from app.services.recipe_book import recipe_result
                result = recipe_result(recipe)
                # Guard against another user's recipe or stale batch quantities.
                batches = list((await session.scalars(select(RecipeBatch).where(
                    RecipeBatch.user_id == profile.id, RecipeBatch.recipe_id == recipe.id,
                    RecipeBatch.remaining_portions > 0,
                ).order_by(RecipeBatch.id))).all())
                if batches and sum(b.remaining_portions for b in batches) + 1e-6 < qty:
                    raise ValueError(f'Not enough prepared portions of {recipe.name}')
                left = qty
                for batch in batches:
                    took = min(left, float(batch.remaining_portions))
                    batch.remaining_portions = round(float(batch.remaining_portions) - took, 3)
                    left -= took
                    if left <= 0:
                        break
                recipe.uses = int(recipe.uses or 0) + 1
                recipe.last_used_at = now_local().replace(tzinfo=None)
            else:
                result = dict(entry.get('nutrition') or {})
            result = normalize_food_result(result)
            result['category'] = category
            result['summary'] = str(entry.get('name') or result.get('summary') or 'Продукт')[:240]
            if recipe is None and result.get('nutrition_source') not in {'label', 'text_estimate'}:
                result['nutrition_source'] = 'text_estimate'
                result['confidence'] = 'low'
            session.info['lr_recipe_id'] = recipe.id if recipe else None
            session.info['lr_portions'] = qty
            food = await record_food_nutrition(
                session, user_id=profile.id, logged_on=day, analysis_id=None,
                category=category, result=result, fraction=qty, portion_label=f'{qty:g} порц.',
            )
            # The hook creates the group's item in the same transaction.
            if category == 'drink':
                count = max(1, int(round(qty)))
                stat.drinks += count
                drink_kind = str(result.get('drink_kind') or 'unknown')
                if recipe and drink_kind == 'unknown':
                    kcal = result.get('calories_kcal')
                    grams = result.get('reference_grams')
                    if kcal is not None and (
                        (grams is not None and grams > 0 and kcal / grams * 100 <= 5) or
                        (grams is None and kcal <= 5)
                    ):
                        drink_kind = 'plain'
                if drink_kind == 'plain':
                    stat.plain_drinks += count
                    for _ in range(min(count, 5)):
                        await record_hydration(session, profile, stat, source='living_rhythm')
                elif drink_kind == 'energy':
                    stat.energy_drinks += count
                else:
                    stat.caloric_drinks += count
            elif category == 'treat':
                total_treat_units += max(1, int(round(qty)))
            categories.append(category)
            kcal = f' · ≈{round(food.calories_kcal):g} ккал' if food.calories_kcal is not None else ' · КБЖУ уточняются'
            lines.append(f'• {result["summary"]} × {qty:g}{kcal}')
        if any(cat == 'meal' for cat in categories) or (meal.kind in {'breakfast', 'lunch', 'dinner'} and any(cat != 'drink' for cat in categories)):
            stat.meals += 1
            rewarded = stat.meals <= 3
            await apply_reward(session, profile, event_type='meal_complete_rhythm' if rewarded else 'meal_rhythm_unrewarded',
                               xp=20 if rewarded else 0, coins=3 if rewarded else 0,
                               payload={'meal_id': meal.id, 'item_count': len(entries)})
        elif any(cat in {'snack', 'treat'} for cat in categories):
            stat.snacks += 1
        # No morality or 'resisted temptation' points. Treats are still visible.
        for _ in range(min(10, total_treat_units)):
            await apply_reward(session, profile, event_type='treat_logged', payload={'meal_id': meal.id})
        proposal.status = 'accepted'
    finally:
        for key in ('lr_group_id', 'lr_recipe_id', 'lr_portions'):
            session.info.pop(key, None)
    return meal, lines


async def breakfast_pattern(session: AsyncSession, user_id: int, today: date, *, min_count: int = 3) -> dict | None:
    """Require >=3 separate historical weekdays and tightly grouped start times."""
    since = today - timedelta(days=84)
    meals = list((await session.scalars(select(RhythmMeal).where(
        RhythmMeal.user_id == user_id, RhythmMeal.kind == 'breakfast',
        RhythmMeal.eaten_on >= since, RhythmMeal.eaten_on < today,
        RhythmMeal.merged_into_id.is_(None),
    ).order_by(RhythmMeal.eaten_on.desc(), RhythmMeal.id.desc()).limit(100))).all())
    matches: dict[date, RhythmMeal] = {}
    for meal in meals:
        if meal.eaten_on.weekday() == today.weekday():
            matches.setdefault(meal.eaten_on, meal)
    selected = list(matches.values())[:9]
    if len(selected) < min_count:
        return None
    minutes = [aware(m.started_at).hour * 60 + aware(m.started_at).minute for m in selected]
    middle = int(median(minutes))
    if max(abs(t - middle) for t in minutes) > 45:
        return None
    signatures = []
    for meal in selected:
        data = await group_items(session, meal.id)
        ids = tuple(sorted(int(item.recipe_id) for item, _ in data if item.recipe_id is not None))
        if ids and len(ids) == len(data):
            signatures.append(ids)
    common, frequency = Counter(signatures).most_common(1)[0] if signatures else ((), 0)
    return {'minute': middle, 'recipe_ids': list(common) if frequency >= min_count else [],
            'sample_count': len(selected)}


async def context_summary(session: AsyncSession, user_id: int) -> str:
    cutoff = now_local() - timedelta(days=7)
    rows = list((await session.scalars(select(RhythmEvent).where(
        RhythmEvent.user_id == user_id, RhythmEvent.happened_at >= cutoff,
    ).order_by(RhythmEvent.id.desc()).limit(12))).all())
    counts = Counter(row.kind for row in rows)
    settings = await get_settings(session, user_id)
    names = {'calm': 'спокойная', 'normal': 'обычная', 'caring': 'заботливая'}
    result = [f'Выбранный стиль инициативы: {names.get(settings.mode, "обычная")}.',
              'Подтверждённый приём пищи может содержать несколько продуктов. Не называй каждый отдельным приёмом.',
              'Не считай отсутствие записи пропуском еды. Желание и план не означают, что продукт съеден.']
    today_rows = list((await session.scalars(select(RhythmMeal).where(
        RhythmMeal.user_id == user_id, RhythmMeal.eaten_on == now_local().date(),
        RhythmMeal.merged_into_id.is_(None),
    ).order_by(RhythmMeal.id.desc()).limit(6))).all())
    if today_rows:
        for meal in reversed(today_rows):
            result.append('Сегодня подтверждено: ' + await group_summary(session, meal))
    if counts['craving']:
        result.append('Иногда пользователь обсуждает желания/вкусняшки: желание не равно съеденной еде.')
    today = now_local().date()
    if any(r.kind == 'travel' and aware(r.happened_at).date() == today for r in rows):
        result.append('СЕГОДНЯ пользователь сообщил о поездке: предлагай варианты еды, удобные в дороге, без оценочных суждений.')
    elif counts['travel']:
        result.append('Бывали дни в дороге; не делай выводов о постоянном режиме из таких дней.')
    try:
        from app.services.activity_breaks import get_break, is_in_effect
        current_break = await get_break(session, user_id)
        if is_in_effect(current_break, today):
            result.append('Подтверждена временная пауза в физических тренировках. Не предлагай зал, интенсивные тренировки или спортивные задания до подтверждённого возобновления.')
    except ImportError:
        pass
    if counts['choice']:
        result.append('Пользователь самостоятельно выбирает, как реагировать на желание; не оценивай выбор как победу или поражение.')
    return '\n'.join(result)

async def today_meals_text(session: AsyncSession, user_id: int, *, day: date | None = None) -> str:
    day = day or now_local().date()
    meals = list((await session.scalars(select(RhythmMeal).where(
        RhythmMeal.user_id == user_id, RhythmMeal.eaten_on == day,
        RhythmMeal.merged_into_id.is_(None),
    ).order_by(RhythmMeal.started_at).limit(25))).all())
    if not meals:
        return 'Подтверждённых записей с подробным составом пока нет.'
    kinds = {'breakfast': '🍳 Завтрак', 'lunch': '🍽 Обед', 'dinner': '🌙 Ужин',
             'snack': '🍎 Перекус', 'unknown': '☕ Отдельная запись'}
    lines = []
    for meal in meals:
        items = await group_items(session, meal.id)
        if not items:
            continue
        names = ', '.join(food.description[:80] for _, food in items[:6])
        known = [float(food.calories_kcal) for _, food in items if food.calories_kcal is not None]
        total = f'≈{sum(known):.0f} ккал' if known else 'КБЖУ неизвестны'
        if len(known) != len(items):
            total += ' (не все позиции рассчитаны)'
        lines.append(f'{aware(meal.started_at):%H:%M} {kinds.get(meal.kind)}: {names} — {total}')
    return '\n'.join(lines)[:3300] if lines else 'Записей пока нет.'

async def suppress_nonessential_autonomy(now: datetime | None = None) -> int:
    """Apply selected initiative frequency to the bot's existing scheduler.

    Only nonessential spontaneous dialogue is touched; workout reminders,
    daily challenge offers and evening reports retain their independent flow.
    """
    from app.db import SessionLocal
    from app.models import Notification
    now = now or now_local()
    suppressed = 0
    async with SessionLocal() as session:
        candidates = list((await session.scalars(select(Notification).where(
            Notification.kind.in_(['autonomous_event', 'activity_nudge']),
            Notification.sent_at.is_(None), Notification.scheduled_at <= now,
        ).order_by(Notification.user_id, Notification.scheduled_at).limit(250))).all())
        for notice in candidates:
            prefs = await get_settings(session, notice.user_id)
            quiet = prefs.mode == 'calm'
            if prefs.mode == 'normal' and notice.kind == 'activity_nudge':
                quiet = True
            if prefs.mode == 'normal' and notice.kind == 'autonomous_event':
                quiet = (notice.payload or {}).get('period') == 'evening'
            if quiet:
                notice.sent_at = now
                suppressed += 1
        await session.commit()
    return suppressed

async def grouped_history_text(session: AsyncSession, user_id: int, start: date, end: date) -> str:
    """A compact cross-reference for the existing historical AI review."""
    meals = list((await session.scalars(select(RhythmMeal).where(
        RhythmMeal.user_id == user_id, RhythmMeal.eaten_on >= start,
        RhythmMeal.eaten_on <= end, RhythmMeal.merged_into_id.is_(None),
    ).order_by(RhythmMeal.eaten_on, RhythmMeal.started_at).limit(120))).all())
    lines = []
    for meal in meals:
        data = await group_items(session, meal.id)
        if not data:
            continue
        title = {'breakfast':'завтрак','lunch':'обед','dinner':'ужин','snack':'перекус'}.get(meal.kind,'отдельная запись')
        names = ', '.join(row.description[:50] for _, row in data[:5])
        lines.append(f'{meal.eaten_on.isoformat()} {title} — {len(data)} позиции: {names}')
    return '\n'.join(lines[-90:]) if lines else ''
