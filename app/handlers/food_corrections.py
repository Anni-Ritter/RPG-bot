"""Food correction UI: inspect -> edit/delete preview -> explicit confirmation.

Natural messages are caught before the normal chat/food recognition routes.
No nutrition write or currency/reward change is performed by the AI.
"""
from __future__ import annotations

import re
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db import SessionLocal
from app.models import FoodNutritionLog
from app.services.food_corrections import (
    FIELDS, apply_correction, checked_log, describe, recent_logs, signature,
    recent_manual_marks, manual_description, undo_manual_mark,
)
from app.services.living_rhythm import now_local
from app.services.rewards import get_or_create_profile
from app.services.typing_status import while_typing
from app.services.ai_engine import ai_enabled
from app.services.ai_features import reserve_ai_call

router = Router()


class FoodCorrectionState(StatesGroup):
    waiting_value = State()
    waiting_confirm = State()


EDIT_WORDS = re.compile(
    r'\b(?:удали|убери|убрать|удалить|исправь|поправь|исправить|'
    r'скорректируй|поменяй|измени|отредактируй|стереть|'
    r'неправильно\s+(?:внесл[аи]?|записал[аи]?|определил[аи]?))\b', re.I,
)
TARGET_STOP = re.compile(r'\b(?:селин|пожалуйста|у\s+меня|последнюю?|сегодняшнюю?|вчерашнюю?|'
                         r'запись|записанн\w*|из\s+дневника|мою|мне|ты|там|было|'
                         r'что|не|ту|уже|из|в|вот|это|и|по|про|на|порцию)\b', re.I)


def is_edit_intent(text: str) -> bool:
    t = (text or '').strip().lower()
    if not t or t.startswith('/'):
        return False
    food_hint = any(w in t for w in ('запис', 'ед', 'напит', 'калор', 'кофе', 'завтрак',
                                  'обед', 'ужин', 'лепёш', 'лепеш', 'перекус', 'продукт',
                                  'порц', 'кбжу', 'слад', 'сыр', 'батонч'))
    explicit = bool(EDIT_WORDS.search(t) and (food_hint or 'последн' in t or 'запись' in t))
    correcting_details = bool(food_hint and re.search(
        r'\b(?:на\s+самом\s+деле|вместо|ошиблась|ошибся|неверно|неправильно)\b', t))
    return explicit or correcting_details


def _words(text: str) -> list[str]:
    return [w[:5] for w in re.findall(r'[а-яa-z0-9]+', text.lower().replace('ё', 'е')) if len(w) >= 4]


def _find_matches(rows: list[FoodNutritionLog], text: str) -> list[FoodNutritionLog]:
    t = text.lower().replace('ё', 'е')
    if 'последн' in t and not any(w in t for w in ('кофе','лепеш','чаю','чай','батонч','слад','суп','завтрак','обед')):
        return rows[:1]
    text = EDIT_WORDS.sub('', t)
    text = TARGET_STOP.sub(' ', text)
    # Ignore quantitative corrections, they are not item names.
    text = re.sub(r'\b\d+[\.,]?\d*\s*(?:г|гр|мл|ккал|б|ж|у)\b', ' ', text)
    query = [w for w in _words(text) if w not in {'калор','сегодня','вчера','порци','грамм','ккал','были','стало','вмест','соста','ингре','колич','совсе','после'}]
    matches = [r for r in rows if query and any(q in _words(r.description) for q in query)]
    if matches:
        if 'сегодня' in t:
            matches = [r for r in matches if r.logged_on == now_local().date()] or matches
        if 'вчера' in t:
            matches = [r for r in matches if r.logged_on == now_local().date() - timedelta(days=1)] or matches
        return matches[:1] if 'последн' in t else matches
    if 'последн' in t:
        return rows[:1]
    return []


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label[:63], callback_data=data) for label, data in line]
        for line in rows
    ])


def list_markup(rows: list[FoodNutritionLog], manual=()) -> InlineKeyboardMarkup:
    buttons = [[(f'{r.logged_on:%d.%m} · {r.description[:37]}', f'fed:open:{r.id}')] for r in rows[:14]]
    buttons += [[(f'🧾 {manual_description(e)[:49]}', f'fed:manual:{e.id}')] for e in list(manual)[:10]]
    return kb(buttons + [[('❌ Закрыть', 'fed:cancel')]])


def item_markup(log: FoodNutritionLog) -> InlineKeyboardMarkup:
    rid = log.id
    return kb([
        [('✏️ Название', f'fed:field:{rid}:name'), ('⚖️ Вес', f'fed:field:{rid}:grams')],
        [('🔥 Калории', f'fed:field:{rid}:kcal'), ('🥗 КБЖУ', f'fed:field:{rid}:macros')],
        [('🗂 Категория', f'fed:field:{rid}:category')],
        [('🧠 Уточнить состав через ИИ', f'fed:field:{rid}:ai')],
        [('🗑 Удалить запись', f'fed:delete:{rid}:{signature(log)}')],
        [('⬅️ К записям', 'fed:list')],
    ])


async def _profile_id(session, actor) -> int:
    profile = await get_or_create_profile(session, actor.id, actor.full_name)
    return profile.id


async def _send_item(message: Message, log: FoodNutritionLog) -> None:
    text = '📖 Запись в дневнике\n' + describe(log)
    text += ('\nБ: ' + _num_text(log.protein_g) + ' · Ж: ' + _num_text(log.fat_g)
             + ' · У: ' + _num_text(log.carbs_g))
    text += '\n\nЧто изменить? Исправления применяются только после подтверждения.'
    await message.answer(text, reply_markup=item_markup(log))


async def _send_delete_question(message: Message, log: FoodNutritionLog) -> None:
    await message.answer('Точно удалить одну запись?\n' + describe(log) +
                         '\n\nКалории и счётчики будут скорректированы. Ранее начисленные XP и монеты сохраняются.',
                         reply_markup=kb([
                             [('🗑 Да, удалить', f'fed:confirmdel:{log.id}:{signature(log)}')],
                             [('❌ Оставить', 'fed:cancel')],
                         ]))


async def show_food_list(message: Message, actor, *, request: str = '') -> None:
    async with SessionLocal() as session:
        uid = await _profile_id(session, actor)
        entries = await recent_logs(session, uid, limit=60)
        manual = await recent_manual_marks(session, uid, limit=10)
    if not entries and not manual:
        await message.answer('В дневнике за последние 30 дней нет записей для правки.')
        return
    if request:
        matches = _find_matches(entries, request)
        if 'последн' in request.lower() and not any(w in request.lower() for w in ('кофе','лепеш','чай','батонч','суп')) and manual:
            matches = []  # An undetailed quick mark might be the actual latest item.
        if matches:
            entries = matches
            if len(matches) == 1:
                if re.search(r'\b(?:удали|убери|убрать|удалить|стереть)\b', request, re.I):
                    await _send_delete_question(message, matches[0])
                else:
                    await _send_item(message, matches[0])
                return
            heading = 'Нашла несколько похожих записей. Уточни, какую исправить:'
        else:
            heading = 'Не уверена, какую запись ты имеешь в виду. Выбери её из журнала:'
    else:
        heading = '📝 Какую запись исправить или удалить? Выбери её ниже (последние 30 дней):'
    if manual:
        heading += '\n🧾 Ручные отметки без КБЖУ можно отменить, но не исправить по граммам.'
    await message.answer(heading, reply_markup=list_markup(entries, manual))


@router.message(Command('food_edit', 'food_fix'))
@router.message(F.text == '📝 Исправить запись')
async def cmd_food_edit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if await state.get_state() in {FoodCorrectionState.waiting_value.state, FoodCorrectionState.waiting_confirm.state}:
        await state.set_state(data.get('food_previous_state'))
        await state.update_data(food_edit_pending=None, food_previous_state=None)
    await show_food_list(message, message.from_user)


@router.message(StateFilter(None), F.text)
async def natural_food_edit(message: Message, state: FSMContext) -> None:
    if is_edit_intent(message.text or ''):
        await show_food_list(message, message.from_user, request=message.text or '')
    else:
        from aiogram.dispatcher.event.bases import SkipHandler
        raise SkipHandler


async def intercept_chat_food_edit(message: Message, actor, text: str) -> bool:
    if not is_edit_intent(text):
        return False
    await show_food_list(message, actor, request=text)
    return True


@router.callback_query(F.data == 'fed:list')
async def callback_list(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if await state.get_state() in {FoodCorrectionState.waiting_value.state, FoodCorrectionState.waiting_confirm.state}:
        await state.set_state(data.get('food_previous_state'))
        await state.update_data(food_edit_pending=None, food_previous_state=None)
    await callback.answer()
    await show_food_list(callback.message, callback.from_user)


@router.callback_query(F.data == 'fed:cancel')
async def callback_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    previous = data.get('food_previous_state')
    if await state.get_state() in {FoodCorrectionState.waiting_value.state, FoodCorrectionState.waiting_confirm.state}:
        await state.set_state(previous)
        await state.update_data(food_edit_pending=None, food_previous_state=None)
    await callback.answer('Без изменений')
    await callback.message.answer('Отменено. В журнале ничего не изменилось.')


@router.callback_query(F.data.startswith('fed:open:'))
async def callback_open(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if await state.get_state() in {FoodCorrectionState.waiting_value.state, FoodCorrectionState.waiting_confirm.state}:
        await state.set_state(data.get('food_previous_state'))
        await state.update_data(food_edit_pending=None, food_previous_state=None)
    try:
        rid = int(callback.data.split(':')[2])
    except (ValueError, IndexError):
        await callback.answer('Неверная кнопка', show_alert=True)
        return
    async with SessionLocal() as session:
        uid = await _profile_id(session, callback.from_user)
        log = await checked_log(session, uid, rid)
    if not log:
        await callback.answer('Эта запись недоступна. Открой журнал заново.', show_alert=True)
        return
    await callback.answer()
    await _send_item(callback.message, log)


def _num_text(x) -> str:
    return '?' if x is None else f'{float(x):g}'


def _prompt(field: str) -> str:
    return {
        'name': 'Напиши правильное название блюда или продукта.',
        'grams': 'Напиши новый вес всей порции в граммах (например, 250). Пересчёт по весу возможен только при известном старом весе.',
        'kcal': 'Напиши правильные калории за эту порцию, числом (например, 220).',
        'macros': 'Напиши четыре числа: ккал, белки, жиры, углеводы. Например: 330 18 12 37.',
        'category': 'Выбери категорию словами: еда, перекус, вкусняшка, напиток.',
        'ai': 'Опиши, что было определено неправильно. Например: «в лепёшке было 80 г сыра вместо 30». ИИ предложит новую ПРИБЛИЗИТЕЛЬНУЮ оценку, которую можно проверить.',
    }[field] + '\nОтменить: /food_edit'


@router.callback_query(F.data.startswith('fed:field:'))
async def callback_field(callback: CallbackQuery, state: FSMContext) -> None:
    parts = (callback.data or '').split(':')
    if len(parts) != 4 or parts[3] not in {'name', 'grams', 'kcal', 'macros', 'category', 'ai'}:
        await callback.answer('Неверная кнопка', show_alert=True)
        return
    try:
        rid = int(parts[2])
    except ValueError:
        await callback.answer('Неверная запись', show_alert=True)
        return
    async with SessionLocal() as session:
        uid = await _profile_id(session, callback.from_user)
        log = await checked_log(session, uid, rid)
    if not log:
        await callback.answer('Запись недоступна', show_alert=True)
        return
    previous = await state.get_state()
    if previous in {FoodCorrectionState.waiting_value.state, FoodCorrectionState.waiting_confirm.state}:
        previous = (await state.get_data()).get('food_previous_state')
    await state.update_data(food_previous_state=previous, food_edit_pending={
        'id': rid, 'signature': signature(log), 'field': parts[3],
    })
    await state.set_state(FoodCorrectionState.waiting_value)
    await callback.answer()
    await callback.message.answer('✏️ ' + describe(log) + '\n\n' + _prompt(parts[3]))


def _parse_value(field: str, text: str, log: FoodNutritionLog) -> dict:
    text = text.strip()
    if field == 'name':
        if len(text) < 2 or len(text) > 300:
            raise ValueError('Название должно содержать от 2 до 300 символов.')
        return {'description': text}
    if field == 'category':
        mapping = {'еда': 'meal', 'прием пищи': 'meal', 'приём пищи': 'meal',
                   'перекус': 'snack', 'вкусняшка': 'treat', 'десерт': 'treat',
                   'напиток': 'drink', 'кофе': 'drink'}
        cat = mapping.get(text.lower())
        if not cat:
            raise ValueError('Напиши: еда / перекус / вкусняшка / напиток.')
        return {'category': cat}
    if field in {'grams', 'kcal', 'macros'} and '-' in text:
        raise ValueError('Отрицательные значения не допускаются.')
    numbers = [float(v.replace(',', '.')) for v in re.findall(r'(?<![\w])\d+(?:[.,]\d+)?', text)]
    if field == 'kcal':
        if len(numbers) != 1 or not 0 <= numbers[0] <= 5000:
            raise ValueError('Нужно одно число от 0 до 5000 (ккал).')
        return {'calories_kcal': round(numbers[0]), 'nutrition_source': 'manual_corrected'}
    if field == 'macros':
        if len(numbers) != 4 or any(n > 5000 for n in numbers) or any(n > 1000 for n in numbers[1:]):
            raise ValueError('Нужны четыре числа: ккал Б Ж У; например 330 18 12 37.')
        return dict(zip(FIELDS, [round(numbers[0]), *(round(n, 1) for n in numbers[1:])])) | {'nutrition_source': 'manual_corrected'}
    if field == 'grams':
        if len(numbers) != 1 or not 0 < numbers[0] <= 5000:
            raise ValueError('Нужен вес от 1 до 5000 г.')
        if log.portion_grams is None or log.portion_grams <= 0:
            raise ValueError('Исходный вес неизвестен, пропорционально пересчитывать нельзя. Используй уточнение через ИИ или исправление КБЖУ вручную.')
        ratio = numbers[0] / log.portion_grams
        result = {'portion_grams': round(numbers[0], 1), 'portion_label': f'{numbers[0]:g} г'}
        for k in FIELDS:
            value = getattr(log, k)
            result[k] = None if value is None else (round(value * ratio) if k == 'calories_kcal' else round(value * ratio, 1))
        return result
    raise ValueError('Неизвестное поле')


@router.message(FoodCorrectionState.waiting_value, F.text)
async def value_received(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    pending = data.get('food_edit_pending') or {}
    if (message.text or '').startswith('/'):
        if message.text.strip().startswith('/food_edit'):
            await state.set_state(data.get('food_previous_state'))
            await state.update_data(food_edit_pending=None, food_previous_state=None)
            await show_food_list(message, message.from_user)
        else:
            await message.answer('Сначала закончи исправление или введи /food_edit для отмены.')
        return
    async with SessionLocal() as session:
        uid = await _profile_id(session, message.from_user)
        log = await checked_log(session, uid, pending.get('id', 0))
        if not log or signature(log) != pending.get('signature'):
            await message.answer('Эта запись уже изменилась. Открой /food_edit ещё раз.')
            await state.set_state(data.get('food_previous_state'))
            await state.update_data(food_edit_pending=None, food_previous_state=None)
            return
        try:
            if pending['field'] == 'ai':
                if not ai_enabled():
                    raise ValueError('ИИ сейчас недоступен. Можно вручную поправить название или КБЖУ.')
                allowed = await reserve_ai_call(session, uid, 'vision')
                if not allowed:
                    raise ValueError('Лимит ИИ-оценок на сегодня исчерпан. Можно поправить цифры вручную.')
                await session.commit()
                from app.services.living_rhythm_ai import estimate_food_text
                from app.services.nutrition import normalize_food_result
                result = normalize_food_result(await while_typing(message, estimate_food_text(
                    'Ранее в пищевом дневнике записано: ' + log.description + '. '
                    'Уточнение пользователя (источник истины для деталей): ' + (message.text or '')[:700] + '. '
                    'Оцени всю ИСПРАВЛЕННУЮ порцию, не только отдельный ингредиент. '
                    'Если деталей недостаточно, не выдумывай точное КБЖУ.'
                )))
                changes = {'description': str(result.get('summary') or log.description)[:300],
                           'nutrition_source': 'text_estimate', 'confidence': 'low'}
                for key in FIELDS:
                    val = result.get(key)
                    changes[key] = None if val is None else float(val)
                # Do not silently turn an ingredient correction into a new drink etc.
                if result.get('reference_grams') is not None:
                    changes['portion_grams'] = float(result['reference_grams'])
                    changes['portion_label'] = f'{changes["portion_grams"]:g} г (оценка)'
            else:
                changes = _parse_value(pending['field'], message.text or '', log)
        except ValueError as exc:
            await message.answer('Не могу подготовить правку: ' + str(exc)[:250] + '\n' + _prompt(pending.get('field', 'name')))
            return
        except Exception as exc:
            print('FOOD_EDIT estimate failed:', repr(exc))
            await message.answer('Сейчас не получилось пересчитать через ИИ. Запись не изменилась. Можно попробовать ещё раз или указать КБЖУ вручную.')
            return
    pending['changes'] = changes
    await state.update_data(food_edit_pending=pending)
    await state.set_state(FoodCorrectionState.waiting_confirm)
    changed = [f'{k}: {v if v is not None else "неизвестно"}' for k, v in changes.items() if k in {'description','portion_grams',*FIELDS,'category'}]
    preview = '📝 Предлагаю исправить\n' + describe(log) + '\n\nБудет:\n' + '\n'.join(changed)
    if pending['field'] == 'ai':
        preview += '\n\n⚠️ Это приблизительный пересчёт ИИ, не данные с этикетки.'
    await message.answer(preview + '\n\nПока ничего не изменено.', reply_markup=kb([
        [('✅ Сохранить исправление', f'fed:apply:{pending["id"]}:{pending["signature"]}')],
        [('❌ Отмена', 'fed:cancel')],
    ]))


@router.message(FoodCorrectionState.waiting_confirm, F.text)
async def confirm_waiting(message: Message) -> None:
    await message.answer('Правка ещё не сохранена. Нажми «✅ Сохранить исправление» под предложением или /food_edit для нового выбора.')


@router.callback_query(F.data.startswith('fed:delete:'))
async def callback_delete(callback: CallbackQuery) -> None:
    parts = (callback.data or '').split(':')
    if len(parts) != 4:
        await callback.answer('Неверная кнопка', show_alert=True)
        return
    try:
        rid = int(parts[2])
    except ValueError:
        await callback.answer('Неверная запись', show_alert=True)
        return
    async with SessionLocal() as session:
        uid = await _profile_id(session, callback.from_user)
        log = await checked_log(session, uid, rid)
    if not log or signature(log) != parts[3]:
        await callback.answer('Запись изменилась. Открой журнал снова.', show_alert=True)
        return
    await callback.answer()
    await _send_delete_question(callback.message, log)


@router.callback_query(F.data.startswith('fed:confirmdel:'))
async def callback_confirm_delete(callback: CallbackQuery) -> None:
    parts = (callback.data or '').split(':')
    if len(parts) != 4:
        await callback.answer('Неверная кнопка', show_alert=True)
        return
    try:
        rid = int(parts[2])
        async with SessionLocal() as session:
            uid = await _profile_id(session, callback.from_user)
            _, totals = await apply_correction(session, uid, rid, parts[3], delete=True)
            await session.commit()
    except Exception as exc:
        await callback.answer('Не получилось удалить. Открой журнал заново.', show_alert=True)
        print('FOOD_EDIT delete:', repr(exc))
        return
    await callback.answer('Удалено')
    await callback.message.answer(f'✅ Удалила запись. Итог за день теперь: {totals["calories_kcal"]} ккал, '
                                  f'Б {totals["protein_g"]} · Ж {totals["fat_g"]} · У {totals["carbs_g"]}.\n'
                                  'Ранее полученные награды не менялись.')


@router.callback_query(F.data.startswith('fed:manual:'))
async def callback_manual_preview(callback: CallbackQuery) -> None:
    try:
        event_id = int((callback.data or '').split(':')[2])
        async with SessionLocal() as session:
            uid = await _profile_id(session, callback.from_user)
            events = await recent_manual_marks(session, uid, limit=250)
            event = next((e for e in events if e.id == event_id), None)
    except (ValueError, IndexError):
        event = None
    if not event:
        await callback.answer('Ручная отметка недоступна', show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        '🧾 ' + manual_description(event) + '\n\n'
        'В этой быстрой отметке нет КБЖУ или названия блюда. Можно отменить её и, если нужно, внести еду заново с подробностями. '
        'Начисленные ранее награды не списываются.',
        reply_markup=kb([
            [('✅ Отменить ручную отметку', f'fed:manualconfirm:{event.id}')],
            [('❌ Оставить', 'fed:cancel')],
        ]),
    )


@router.callback_query(F.data.startswith('fed:manualconfirm:'))
async def callback_manual_confirm(callback: CallbackQuery) -> None:
    try:
        event_id = int((callback.data or '').split(':')[2])
        async with SessionLocal() as session:
            uid = await _profile_id(session, callback.from_user)
            label, day = await undo_manual_mark(session, uid, event_id)
            await session.commit()
    except Exception as exc:
        print('FOOD_EDIT manual undo:', repr(exc))
        await callback.answer('Эта отметка недоступна или уже отменена.', show_alert=True)
        return
    await callback.answer('Отменено')
    await callback.message.answer(f'✅ Отменила: {label} · {day:%d.%m}. Счётчики обновила. Старые награды не меняла.')


@router.callback_query(F.data.startswith('fed:apply:'))
async def callback_apply(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != FoodCorrectionState.waiting_confirm.state:
        await callback.answer('Исправление устарело. Начни через /food_edit.', show_alert=True)
        return
    data = await state.get_data()
    pending = data.get('food_edit_pending') or {}
    parts = (callback.data or '').split(':')
    if len(parts) != 4 or str(pending.get('id')) != parts[2] or pending.get('signature') != parts[3]:
        await callback.answer('Это не текущая правка. Начни через /food_edit.', show_alert=True)
        return
    try:
        async with SessionLocal() as session:
            uid = await _profile_id(session, callback.from_user)
            row, totals = await apply_correction(session, uid, int(pending['id']),
                                                   pending['signature'], pending['changes'])
            await session.commit()
    except Exception as exc:
        await callback.answer('Запись изменилась или правка не прошла.', show_alert=True)
        print('FOOD_EDIT apply:', repr(exc))
        return
    await state.set_state(data.get('food_previous_state'))
    await state.update_data(food_edit_pending=None, food_previous_state=None)
    await callback.answer('Исправлено')
    await callback.message.answer('✅ Исправила запись: ' + describe(row) +
                                  f'\n\nЗа день: {totals["calories_kcal"]} ккал · '
                                  f'Б {totals["protein_g"]} · Ж {totals["fat_g"]} · У {totals["carbs_g"]}.\n'
                                  'Новых наград за правку нет; старые сохраняются.')
