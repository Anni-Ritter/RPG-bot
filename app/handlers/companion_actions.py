"""Converse first; turn clear category edits into verified database actions."""
from __future__ import annotations

import logging
from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import StateFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from app.db import SessionLocal
from app.services.companion_actions import category_request, requested_category, requested_calorie_fix, matching_records
from app.services.food_corrections import apply_correction, checked_log, recent_logs, signature, describe
from app.services.rewards import get_or_create_profile
from sqlalchemy import select
from app.models import GameEvent

router = Router()
log = logging.getLogger('selin.actions')
LABELS = {'meal':'полноценная еда','snack':'перекус','treat':'вкусняшка','drink':'напиток'}


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label[:60], callback_data=data) for label,data in line]
        for line in rows
    ])


def _options(item_id: int, sig: str):
    return keyboard([
        [('🍲 Полноценная еда', f'ca:set:{item_id}:{sig}:meal'),('🍎 Перекус', f'ca:set:{item_id}:{sig}:snack')],
        [('🍬 Вкусняшка', f'ca:set:{item_id}:{sig}:treat'),('☕ Напиток', f'ca:set:{item_id}:{sig}:drink')],
    ])


async def handle_category_message(message: Message, actor, text: str) -> bool:
    if not category_request(text):
        return False
    target = requested_category(text)
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, actor.id, actor.full_name)
        entries = await recent_logs(session, profile.id, limit=60)
        matches = matching_records(entries, text)
        if len(matches) == 1 and target:
            chosen = matches[0]
            if chosen.category == target:
                result = f'— Проверила дневник: {chosen.description} уже отмечен как «{LABELS[target]}». Ничего менять не пришлось.'
            else:
                try:
                    corrected, totals = await apply_correction(
                        session, profile.id, chosen.id, signature(chosen), {'category': target})
                    await session.commit()
                    await session.refresh(corrected)
                    if corrected.category != target:
                        raise RuntimeError('Database category did not match requested category')
                    result = (f'✅ Исправила в дневнике: «{corrected.description}» теперь «{LABELS[target]}». '
                              f'КБЖУ не меняла. Сегодня записано {totals["calories_kcal"]} ккал (по имеющимся записям).')
                except Exception:
                    log.exception('Category edit failed')
                    await session.rollback()
                    result = '— Не смогла применить изменение. Запись осталась прежней. Попробуй /food_edit.'
            await message.answer(result)
            return True
    if not matches:
        await message.answer('— Не нашла однозначную запись. Открой /food_edit и выбери нужный продукт — ничего пока не меняла.')
        return True
    if len(matches) == 1 and not target:
        row = matches[0]
        await message.answer(f'— Нашла «{row.description}». На какую категорию поменять? Пока ничего не меняла.',
                             reply_markup=_options(row.id, signature(row)))
        return True
    # Multiple matches: choose a DB-owned record first, no guesswork.
    options = [ [(f'{row.logged_on:%d.%m} · {row.description[:38]}',
                  f'ca:pick:{row.id}:{signature(row)}:{target or "ask"}')] for row in matches[:7]]
    await message.answer('— Нашла несколько похожих записей. Какую исправить? Пока ничего не меняла.',
                         reply_markup=keyboard(options))
    return True


async def handle_numeric_message(message: Message, actor, text: str) -> bool:
    value = requested_calorie_fix(text)
    if value is None:
        return False
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, actor.id, actor.full_name)
        matches = matching_records(await recent_logs(session, profile.id, limit=60), text)
        if len(matches) == 1:
            record = matches[0]
            name = record.description
            if record.calories_kcal is not None and round(record.calories_kcal) == value:
                await message.answer(f'— Проверила: у «{name}» уже {value} ккал. Ничего не меняла.')
                return True
            try:
                corrected, _ = await apply_correction(session, profile.id, record.id, signature(record),
                                                     {'calories_kcal':value,'nutrition_source':'manual_corrected'})
                await session.commit()
                await session.refresh(corrected)
                if round(corrected.calories_kcal) != value:
                    raise RuntimeError('Calorie correction did not stick')
                await message.answer(f'✅ Исправила калории «{name}»: {value} ккал. Белки, жиры и углеводы не меняла.')
            except Exception:
                log.exception('Calorie edit failed')
                await session.rollback()
                await message.answer('— Не удалось изменить калории. Запись оставила прежней. Можно через /food_edit.')
            return True
    if not matches:
        await message.answer('— Не нашла продукт для изменения калорий. Открой /food_edit, пока ничего не меняла.')
        return True
    choices = [[(f'{r.logged_on:%d.%m} · {r.description[:35]}',
                 f'ca:kcal:{r.id}:{signature(r)}:{value}')] for r in matches[:7]]
    await message.answer('— Нашла несколько подходящих записей. Какую исправить? Пока ничего не меняла.',
                         reply_markup=keyboard(choices))
    return True


async def handle_conversational_action(message: Message, actor, text: str) -> bool:
    return (await handle_category_message(message, actor, text)
            or await handle_numeric_message(message, actor, text))


@router.message(Command('selin_actions'))
async def last_real_actions(message: Message) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        events = list((await session.scalars(select(GameEvent).where(
            GameEvent.user_id == profile.id,
            GameEvent.event_type.in_(['food_log_deleted','food_log_corrected','meal_complete_rhythm','meal_rhythm_unrewarded'])
        ).order_by(GameEvent.id.desc()).limit(6))).all())
    if not events:
        await message.answer('— Подтверждённых правок и записей приёмов пока нет. Реплики ИИ здесь не учитываются.')
        return
    labels = {'food_log_deleted':'Удаление еды','food_log_corrected':'Правка еды',
              'meal_complete_rhythm':'Запись приёма пищи','meal_rhythm_unrewarded':'Запись приёма пищи'}
    lines = [f'• {labels.get(e.event_type)} · #{(e.payload or {}).get("log_id") or (e.payload or {}).get("meal_id") or e.id}'
             for e in events]
    await message.answer('📋 Реальные действия в базе:\n'+'\n'.join(lines))


@router.callback_query(F.data.startswith('ca:kcal:'))
async def callback_kcal(callback: CallbackQuery) -> None:
    parts=(callback.data or '').split(':')
    try:
        if len(parts) != 5:
            raise ValueError('bad callback')
        rid, sig, value = int(parts[2]), parts[3], int(parts[4])
        if not 0 <= value <= 5000:
            raise ValueError('invalid amount')
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
            record = await checked_log(session, profile.id, rid)
            if record is None or signature(record) != sig:
                await callback.answer('Запись изменилась, начни заново',show_alert=True); return
            name=record.description
            if record.calories_kcal is None or round(record.calories_kcal) != value:
                record, _ = await apply_correction(session, profile.id, rid, sig,
                                                   {'calories_kcal':value,'nutrition_source':'manual_corrected'})
                await session.commit()
                await session.refresh(record)
                if round(record.calories_kcal) != value:
                    raise RuntimeError('Not persisted')
        await callback.answer('Проверено')
        await callback.message.answer(f'✅ В дневнике: {name} — {value} ккал. Другие КБЖУ не меняла.')
    except Exception:
        log.exception('Calorie callback failed')
        await callback.answer('Не получилось исправить запись',show_alert=True)


@router.message(StateFilter(None), F.text)
async def direct_category(message: Message, state: FSMContext) -> None:
    if not await handle_conversational_action(message, message.from_user, message.text or ''):
        raise SkipHandler


@router.callback_query(F.data.startswith('ca:pick:'))
async def choose_record(callback: CallbackQuery) -> None:
    fields = (callback.data or '').split(':')
    if len(fields) != 5:
        await callback.answer('Кнопка устарела',show_alert=True); return
    try:
        rid = int(fields[2]); sig, target = fields[3:5]
    except (ValueError, IndexError):
        await callback.answer('Неверная запись',show_alert=True); return
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        record = await checked_log(session, profile.id, rid)
    if record is None or signature(record) != sig:
        await callback.answer('Запись уже изменилась',show_alert=True); return
    if target in LABELS:
        await _apply(callback, rid, sig, target)
    else:
        await callback.answer()
        await callback.message.answer('— Нашла «'+record.description+'». Выбери категорию:',
                                      reply_markup=_options(rid,sig))


async def _apply(callback: CallbackQuery, rid: int, sig: str, target: str) -> None:
    try:
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
            record = await checked_log(session, profile.id, rid)
            if record is None or signature(record) != sig:
                await callback.answer('Запись изменилась, выбери заново',show_alert=True); return
            name = record.description
            if record.category != target:
                record, totals = await apply_correction(session, profile.id, rid, sig, {'category':target})
                await session.commit()
                await session.refresh(record)
                if record.category != target:
                    raise RuntimeError('Category not applied')
            else:
                totals = None
        await callback.answer('Проверено')
        await callback.message.answer(
            f'✅ В дневнике: «{name}» — «{LABELS[target]}». КБЖУ без изменений.'
            if totals is not None else f'— Проверила: «{name}» уже записан как «{LABELS[target]}».')
    except Exception:
        log.exception('Category callback failed')
        await callback.answer('Не получилось изменить; запись не подтверждена', show_alert=True)


@router.callback_query(F.data.startswith('ca:set:'))
async def set_category(callback: CallbackQuery) -> None:
    fields = (callback.data or '').split(':')
    if len(fields) != 5 or fields[4] not in LABELS:
        await callback.answer('Кнопка устарела',show_alert=True); return
    try:
        rid = int(fields[2])
    except ValueError:
        await callback.answer('Неверная запись',show_alert=True); return
    await _apply(callback, rid, fields[3], fields[4])
