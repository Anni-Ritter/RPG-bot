"""Small periodic checks: deferred meal advice, optional meal merge, learned breakfast."""
from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select, func, delete

from app.db import SessionLocal
from app.models import Notification, UserProfile, FoodNutritionLog
from app.services.ai_engine import ai_enabled, generate_intraday_nutrition_advice
from app.services.ai_features import build_ai_context, get_coach_rules, reserve_ai_call
from app.services.assets import send_reaction_to_chat
from app.services.intraday_coach import _entries_text
from app.services.living_rhythm import (
    RhythmSettings, RhythmMeal, RhythmMealItem, RhythmEvent, now_local, aware,
    breakfast_pattern, get_settings, group_items, group_summary,
)


def merge_keyboard(a_id: int, b_id: int):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Да, это вместе', callback_data=f'rhythm:merge:{a_id}:{b_id}:yes')],
        [InlineKeyboardButton(text='Нет, отдельно', callback_data=f'rhythm:merge:{a_id}:{b_id}:no')],
    ])


def treat_keyboard():
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🚗 Я в дороге', callback_data='rhythm:treat:travel')],
        [InlineKeyboardButton(text='🍲 Давно нормально не ела', callback_data='rhythm:treat:long_gap')],
        [InlineKeyboardButton(text='🍰 Просто хочется вкусного', callback_data='rhythm:treat:pleasure')],
    ])


def routine_keyboard(notification_id: int, *, has_recipes: bool):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    rows = []
    if has_recipes:
        rows.append([InlineKeyboardButton(text='✅ Да, как обычно', callback_data=f'rhythm:routine:{notification_id}:yes')])
    rows.extend([
        [InlineKeyboardButton(text='✏️ Сегодня было другое', callback_data=f'rhythm:routine:{notification_id}:different')],
        [InlineKeyboardButton(text='💤 Ещё не завтракала', callback_data=f'rhythm:routine:{notification_id}:later')],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def ask_merge_for_recent_pairs(bot) -> None:
    now = now_local()
    async with SessionLocal() as session:
        groups = list((await session.scalars(select(RhythmMeal).where(
            RhythmMeal.eaten_on == now.date(), RhythmMeal.merged_into_id.is_(None),
            RhythmMeal.merge_prompt_sent.is_(False), RhythmMeal.feedback_sent_at.is_(None),
            RhythmMeal.started_at >= now - timedelta(minutes=50),
        ).order_by(RhythmMeal.user_id, RhythmMeal.started_at))).all())
        # Only prompt for a confirmed full food entry followed soon by a drink.
        for later in groups:
            later_items = await group_items(session, later.id)
            if len(later_items) != 1 or later_items[0][0].category != 'drink':
                continue
            previous = list((await session.scalars(select(RhythmMeal).where(
                RhythmMeal.user_id == later.user_id, RhythmMeal.eaten_on == later.eaten_on,
                RhythmMeal.id != later.id, RhythmMeal.merged_into_id.is_(None),
                RhythmMeal.started_at < later.started_at,
                RhythmMeal.started_at >= later.started_at - timedelta(minutes=20),
            ).order_by(RhythmMeal.started_at.desc()).limit(3))).all())
            earlier = None
            for candidate in previous:
                items = await group_items(session, candidate.id)
                if any(it.category == 'meal' for it, _ in items):
                    earlier = candidate
                    break
            if not earlier:
                continue
            prefs = await get_settings(session, later.user_id)
            if prefs.mode == 'calm':
                # Calm mode is genuinely quiet. Users can still ask to merge.
                later.merge_prompt_sent = True
                continue
            later.merge_prompt_sent = True
            profile = await session.get(UserProfile, later.user_id)
            if not profile:
                continue
            summary = await group_summary(session, later)
            try:
                await bot.send_message(profile.telegram_id,
                    f'— {summary}. Это было вместе с недавним приёмом пищи? '\
                    'Могу объединить в журнале без повторного подсчёта.',
                    reply_markup=merge_keyboard(earlier.id, later.id))
            except Exception as exc:
                print('[LIVING_RHYTHM] merge prompt:', repr(exc))
        await session.commit()


async def ask_about_treats(bot) -> None:
    today = now_local().date()
    async with SessionLocal() as session:
        profiles = list((await session.scalars(select(UserProfile))).all())
        for profile in profiles:
            settings = await get_settings(session, profile.id)
            if settings.mode == 'calm' or settings.last_treat_question == today:
                continue
            items = list((await session.execute(select(RhythmMealItem, RhythmMeal).join(
                RhythmMeal, RhythmMeal.id == RhythmMealItem.meal_id
            ).where(RhythmMeal.user_id == profile.id, RhythmMeal.eaten_on == today,
                    RhythmMeal.merged_into_id.is_(None), RhythmMealItem.category == 'treat'))).all())
            # Multiple sweets inside lunch are not four separate sweet snacks.
            from app.services.ai_features import daily_treat_count
            total_treats = await daily_treat_count(session, profile.id, today)
            # Sweet items grouped inside a main meal are not independent snacks.
            inside_meals = sum(min(5, max(1, int(round(item.portions))))
                               for item, meal in items if meal.kind in {'breakfast', 'lunch', 'dinner'})
            treats = max(0, total_treats - inside_meals)
            if treats < 4:
                continue
            settings.last_treat_question = today
            await session.commit()
            try:
                await send_reaction_to_chat(bot, profile.telegram_id, 'selin', 'curious',
                    '— Слушай, сегодня вкусняшек уже несколько. Ты успевала нормально поесть, '
                    'или весь день в разъездах? А может, просто хотелось чего-то вкусного?',
                    reply_markup=treat_keyboard())
            except Exception as exc:
                print('[LIVING_RHYTHM] treats question:', repr(exc))


async def send_grouped_feedback(bot) -> None:
    now = now_local()
    async with SessionLocal() as session:
        groups = list((await session.scalars(select(RhythmMeal).where(
            RhythmMeal.eaten_on == now.date(), RhythmMeal.merged_into_id.is_(None),
            RhythmMeal.feedback_sent_at.is_(None),
            RhythmMeal.last_item_at <= now - timedelta(minutes=12),
        ).order_by(RhythmMeal.last_item_at).limit(40))).all())
        for group in groups:
            settings = await get_settings(session, group.user_id)
            rows = await group_items(session, group.id)
            # Never send a meal review while a newly entered drink may still be
            # merged into that meal. One review after a completed breakfast is
            # preferable to an immediate correction after each product.
            if any(item.category == 'meal' for item, _ in rows):
                recent_drink = await session.scalar(select(RhythmMeal).where(
                    RhythmMeal.user_id == group.user_id,
                    RhythmMeal.eaten_on == group.eaten_on,
                    RhythmMeal.id != group.id,
                    RhythmMeal.merged_into_id.is_(None),
                    RhythmMeal.merge_prompt_sent.is_(True),
                    RhythmMeal.started_at >= group.started_at,
                    RhythmMeal.started_at <= group.last_item_at + timedelta(minutes=20),
                ).order_by(RhythmMeal.started_at.desc()).limit(1))
                if recent_drink and now - aware(recent_drink.last_item_at) < timedelta(minutes=30):
                    continue
            # Never spend AI calls on one solitary drink, or on unconfirmed info.
            meaningful = any(item.category == 'meal' for item, _ in rows) or len(rows) >= 2
            group.feedback_sent_at = now
            if settings.mode == 'calm' or not meaningful:
                continue
            if settings.feedback_day != now.date():
                settings.feedback_day = now.date()
                settings.feedback_count = 0
            max_per_day, cooldown = (3, 90) if settings.mode == 'caring' else (2, 120)
            if (int(settings.feedback_count or 0) >= max_per_day or
                settings.last_feedback_at and aware(settings.last_feedback_at) >= now - timedelta(minutes=cooldown)):
                continue
            if not ai_enabled():
                continue
            allowed = await reserve_ai_call(session, group.user_id, 'initiative')
            if not allowed:
                continue
            profile = await session.get(UserProfile, group.user_id)
            if profile is None:
                continue
            context = await build_ai_context(session, profile)
            coach_rules = await get_coach_rules(session, group.user_id)
            entries = list((await session.scalars(select(FoodNutritionLog).where(
                FoodNutritionLog.user_id == group.user_id,
                FoodNutritionLog.logged_on == now.date(),
            ).order_by(FoodNutritionLog.id))).all())
            from app.services.living_rhythm import group_summary
            summary = await group_summary(session, group)
            settings.last_feedback_at = now
            settings.feedback_count = int(settings.feedback_count or 0) + 1
            await session.commit()
            try:
                response = await generate_intraday_nutrition_advice(
                    context=context,
                    entries_text=_entries_text(entries),
                    coach_rules=coach_rules + '\n'
                        'НЕ ругай за сладкое. Цель в ккал — ориентир, не потолок. '
                        'Дай один полезный совет только когда есть основания; не делай выводы об общем питании по неполным записям.',
                    latest_action=f'Завершился подтверждённый {summary}. Несколько позиций одного приёма должны получить один общий разбор.',
                    current_time=now.strftime('%H:%M'),
                    day_phase=('утро' if now.hour < 12 else 'день' if now.hour < 17 else 'вечер'),
                )
                if response.get('should_send') and str(response.get('text') or '').strip():
                    await send_reaction_to_chat(bot, profile.telegram_id, 'selin',
                                                str(response.get('emotion') or 'neutral'),
                                                '🧭 После приёма пищи\n\n' + str(response['text'])[:1400])
            except Exception as exc:
                print('[LIVING_RHYTHM] delayed feedback:', repr(exc))
        await session.commit()


async def learned_breakfast_reminders(bot) -> None:
    now = now_local()
    if not 5 <= now.hour <= 11:
        return
    async with SessionLocal() as session:
        profiles = list((await session.scalars(select(UserProfile))).all())
        for profile in profiles:
            settings = await get_settings(session, profile.id)
            if settings.mode == 'calm' or settings.last_breakfast_nudge == now.date():
                continue
            min_count = 2 if settings.mode == 'caring' else 3
            pattern = await breakfast_pattern(session, profile.id, now.date(), min_count=min_count)
            if not pattern:
                continue
            minute = now.hour * 60 + now.minute
            if not pattern['minute'] + 25 <= minute <= pattern['minute'] + 75:
                continue
            today_breakfast = await session.scalar(select(RhythmMeal.id).where(
                RhythmMeal.user_id == profile.id, RhythmMeal.eaten_on == now.date(),
                RhythmMeal.kind == 'breakfast', RhythmMeal.merged_into_id.is_(None)
            ).limit(1))
            if today_breakfast is not None:
                continue
            # Includes older one-tap/manual records with no meal detail.
            from app.models import DailyStat
            day_stat = await session.scalar(select(DailyStat.meals).where(
                DailyStat.user_id == profile.id, DailyStat.stat_date == now.date()
            ).limit(1))
            if day_stat and int(day_stat) > 0:
                continue
            if settings.mode == 'normal' and now.weekday() >= 5:
                # Prefer a calm weekend; still learns weekend patterns.
                continue
            ids = list(pattern['recipe_ids'])
            if ids:
                try:
                    from app.models import SavedRecipe
                    saved = list((await session.scalars(select(SavedRecipe).where(
                        SavedRecipe.user_id == profile.id, SavedRecipe.id.in_(ids)
                    ))).all())
                    if len(saved) != len(set(ids)):
                        ids = []
                except ImportError:
                    ids = []
            settings.last_breakfast_nudge = now.date()
            key = f'rhythm:{profile.id}:{now.date()}:breakfast'
            note = Notification(user_id=profile.id, kind='rhythm_breakfast',
                                scheduled_at=now, sent_at=now, dedupe_key=key,
                                payload={'status': 'open', 'recipe_ids': ids})
            session.add(note)
            await session.flush()
            await session.commit()
            try:
                await bot.send_message(profile.telegram_id,
                    '— Ты обычно к этому времени уже завтракаешь. Сегодня тоже всё по привычному плану '
                    'или утро пошло иначе? Я ничего не записывала.',
                    reply_markup=routine_keyboard(note.id, has_recipes=bool(ids)))
            except Exception as exc:
                print('[LIVING_RHYTHM] breakfast nudge:', repr(exc))
        await session.commit()


async def rhythm_tick(bot) -> None:
    await ask_merge_for_recent_pairs(bot)
    await ask_about_treats(bot)
    await send_grouped_feedback(bot)
    await learned_breakfast_reminders(bot)
    # Ephemeral context need not accumulate forever.
    async with SessionLocal() as session:
        await session.execute(delete(RhythmEvent).where(RhythmEvent.happened_at < now_local() - timedelta(days=14)))
        await session.commit()


async def rhythm_worker(bot) -> None:
    await asyncio.sleep(12)
    while True:
        try:
            await rhythm_tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print('[LIVING_RHYTHM] worker:', repr(exc))
        await asyncio.sleep(60)
