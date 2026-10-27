"""Confirmable conversational food, meal groups, and quietness preferences."""
from __future__ import annotations

import asyncio
from datetime import timedelta

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db import SessionLocal
from app.services.ai_engine import ai_enabled, generate_selin_reply
from app.services.ai_features import (
    append_recent_message, build_ai_context, get_or_create_ai_state,
    recent_chat_text, reserve_ai_call,
)
from app.services.assets import send_reaction
from app.services.challenge_feedback import check_and_notify_challenges
from app.services.living_rhythm import (
    KINDS, MODES, RhythmMeal, RhythmProposal, RhythmEvent, RhythmSettings,
    append_event, aware, create_proposal, get_settings, merge_group,
    now_local, record_confirmed_group,
)
from app.services.living_rhythm_ai import enrich_confirmable_items, interpret, likely_relevant, recipe_match
from app.services.rewards import get_or_create_profile

router = Router()
_processing: set[int] = set()
_confirming: set[int] = set()


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in line]
        for line in rows
    ])


LABELS = {'breakfast': 'Завтрак', 'lunch': 'Обед', 'dinner': 'Ужин',
          'snack': 'Перекус', 'unknown': 'Приём пищи'}
MODE_LABELS = {'calm': 'Спокойная', 'normal': 'Обычная', 'caring': 'Заботливая'}


def proposal_markup(proposal: RhythmProposal) -> InlineKeyboardMarkup:
    pid = proposal.id
    return kb([
        [('✅ Да, всё верно — записать', f'rhythm:confirm:{pid}')],
        [('🍳 Завтрак', f'rhythm:kind:{pid}:breakfast'),
         ('🍽 Обед', f'rhythm:kind:{pid}:lunch')],
        [('🌙 Ужин', f'rhythm:kind:{pid}:dinner'),
         ('🍎 Перекус', f'rhythm:kind:{pid}:snack')],
        [('❌ Не записывать', f'rhythm:cancel:{pid}')],
    ])


def proposal_text(proposal: RhythmProposal) -> str:
    lines = [f'🍽 Селин предлагает записать это как {LABELS.get(proposal.kind, "приём пищи").lower()}:']
    for item in list(proposal.items or []):
        qty = float(item.get('quantity') or 1)
        kcal = item.get('calories_kcal')
        value = f'≈{float(kcal) * qty:.0f} ккал' if kcal is not None else 'КБЖУ пока неизвестны'
        origin = 'сохранённый рецепт' if item.get('recipe_id') else 'приблизительная оценка по тексту'
        lines.append(f'• {item.get("name", "Продукт")} × {qty:g} — {value} ({origin})')
    lines.extend(['', 'Пока это только предложение: в дневник ничего не внесено.',
                  'Проверь позиции и приём пищи. Для другого состава лучше прислать уточнение.'])
    return '\n'.join(lines)[:3500]


async def _get_interpretation(user_id: int, text: str) -> dict | None:
    if user_id in _processing or not likely_relevant(text):
        return None
    _processing.add(user_id)
    try:
        return await interpret(text)
    finally:
        _processing.discard(user_id)


async def _offer(message: Message, actor, parsed: dict) -> bool:
    """The only path from an AI extraction to food is a reviewed proposal."""
    if parsed['intent'] != 'consumed' or not parsed['items']:
        return False
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, actor.id, actor.full_name)
        # Saved recipes are cheap to repeat: don't consume vision quota.
        needs_estimate = False
        for item in parsed['items']:
            matched = await recipe_match(session, profile.id, item['name'])
            if matched is None or matched.category != item['category']:
                needs_estimate = True
                break
        allowed = (await reserve_ai_call(session, profile.id, 'vision')) if needs_estimate else True
        await session.commit()
    if not allowed:
        await message.answer('Сегодня лимит распознавания исчерпан. Запиши еду через обычное меню.')
        return True
    try:
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, actor.id, actor.full_name)
            items = await enrich_confirmable_items(session, profile.id, parsed['items'])
            kind = parsed['meal_kind']
            if kind not in KINDS or kind == 'unknown':
                if all(item['category'] in {'snack', 'treat'} for item in items):
                    kind = 'snack'
                elif any(item['category'] == 'meal' for item in items):
                    from app.services.living_rhythm import kind_at
                    kind = kind_at(now_local(), 'meal')
                else:
                    kind = 'unknown'
            proposal = await create_proposal(session, profile.id, kind, items)
            await session.commit()
            text = proposal_text(proposal)
            markup = proposal_markup(proposal)
    except Exception as exc:
        print('[LIVING_RHYTHM] preview error:', repr(exc))
        await message.answer('Не смогла безопасно подготовить запись. Попробуй уточнить порции или записать через фото.')
        return True
    await message.answer(text, reply_markup=markup)
    return True


async def _observe_non_consumption(actor, parsed: dict) -> None:
    kind = str(parsed.get('intent') or '')
    event_kind = 'craving' if kind == 'craving' else 'choice' if kind == 'choice' else ''
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, actor.id, actor.full_name)
        if event_kind:
            await append_event(session, profile.id, event_kind)
        if parsed.get('travel_today'):
            await append_event(session, profile.id, 'travel')
        await session.commit()


async def handle_chat_capture(message: Message, actor, text: str) -> bool:
    """Call this at the start of free/initiative Selin chat message handlers.

    Returns True ONLY when a food proposal or clarification takes over the turn;
    cravings and choices continue into regular natural dialogue.
    """
    if not likely_relevant(text):
        return False
    try:
        parsed = await _get_interpretation(actor.id, text)
    except Exception as exc:
        print('[LIVING_RHYTHM] intent failed:', repr(exc))
        return False
    if not parsed:
        return False
    if parsed.get('skip_training_today'):
        await maybe_offer_training_skip(message, actor, parsed)
    if parsed['intent'] == 'consumed':
        if not parsed['items']:
            await message.answer(parsed.get('clarifying_question') or 'Уточни, что именно и сколько ты съела или выпила?')
            return True
        await _observe_non_consumption(actor, parsed)
        return await _offer(message, actor, parsed)
    await _observe_non_consumption(actor, parsed)
    return False


@router.message(StateFilter(None), F.text)
async def live_text_message(message: Message, state: FSMContext) -> None:
    text = (message.text or '').strip()
    if not likely_relevant(text):
        raise SkipHandler
    try:
        parsed = await _get_interpretation(message.from_user.id, text)
    except Exception as exc:
        print('[LIVING_RHYTHM] intent failed:', repr(exc))
        raise SkipHandler
    if not parsed:
        raise SkipHandler
    if parsed.get('skip_training_today'):
        await maybe_offer_training_skip(message, message.from_user, parsed)
    if parsed['intent'] == 'consumed':
        if not parsed['items']:
            await message.answer(parsed.get('clarifying_question') or 'Сколько и чего именно было?')
            return
        await _observe_non_consumption(message.from_user, parsed)
        await _offer(message, message.from_user, parsed)
        return
    if parsed['intent'] not in {'craving', 'choice', 'context'}:
        raise SkipHandler
    await _observe_non_consumption(message.from_user, parsed)
    # Start an ordinary Selin conversation. Food is never automatically logged.
    if not ai_enabled():
        raise SkipHandler
    from app.handlers.ai_chat import FreeSelinChatState
    from app.services.story import (advance_week1_if_due, get_or_create_story_progress,
                                    initialize_week1_v2, selin_chat_gate)
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        progress = await get_or_create_story_progress(session, profile.id, now_local().date())
        initialize_week1_v2(progress, profile, now_local().date())
        advance_week1_if_due(progress, now_local().date())
        allowed, _ = selin_chat_gate(profile, progress, now_local().date())
        if not allowed:
            await session.commit()
            raise SkipHandler
        await reserve_ai_call(session, profile.id, 'chat')
        context = await build_ai_context(session, profile)
        ai_state = await get_or_create_ai_state(session, profile)
        recent = recent_chat_text(ai_state)
        await session.commit()
    try:
        response = await generate_selin_reply(user_text=text, context=context,
                                              recent_chat=recent, can_offer_quest=False)
    except Exception:
        raise SkipHandler
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        ai_state = await get_or_create_ai_state(session, profile)
        append_recent_message(ai_state, 'user', text)
        append_recent_message(ai_state, 'assistant', str(response.get('text') or ''))
        await session.commit()
    await state.set_state(FreeSelinChatState.talking)
    await state.update_data(last_chat_activity_at=now_local().isoformat())
    await send_reaction(message, 'selin', str(response.get('emotion') or 'neutral'),
                        str(response.get('text') or 'Я слушаю.'))


@router.message(Command('rhythm'))
async def rhythm_settings_command(message: Message) -> None:
    await show_settings(message, message.from_user.id)


@router.message(F.text == '⚙️ Живой ритм')
async def rhythm_settings_text(message: Message) -> None:
    await show_settings(message, message.from_user.id)


async def show_settings(message: Message, user_id: int) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, user_id, None)
        settings = await get_settings(session, profile.id)
        mode = settings.mode
        await session.commit()
    await message.answer(
        f'🌿 Живой ритм Селин\n\nТекущая активность: {MODE_LABELS.get(mode)}.\n\n'
        'Спокойная — без инициативных разборов еды и подсказок; плановые игровые уведомления остаются.\n'
        'Обычная — редкие полезные подсказки после еды и напоминания об устойчивой привычке.\n'
        'Заботливая — можно немного чаще, но без спама.\n\n'
        'Ни один из режимов не записывает еду без подтверждения.',
        reply_markup=kb([[('💤 Спокойная', 'rhythm:mode:calm')],
                         [('🌿 Обычная', 'rhythm:mode:normal')],
                         [('🦊 Заботливая', 'rhythm:mode:caring')]])
    )


@router.callback_query(F.data.startswith('rhythm:mode:'))
async def change_mode(callback: CallbackQuery) -> None:
    mode = callback.data.rsplit(':', 1)[-1]
    if mode not in MODES:
        await callback.answer('Неизвестный режим', show_alert=True)
        return
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        settings = await get_settings(session, profile.id)
        settings.mode = mode
        await session.commit()
    await callback.answer('Сохранила')
    await callback.message.edit_text(f'🌿 Активность Селин: {MODE_LABELS[mode]}.\nИзменить можно через /rhythm.')


@router.callback_query(F.data.startswith('rhythm:kind:'))
async def change_kind(callback: CallbackQuery) -> None:
    parts = callback.data.split(':')
    if len(parts) != 4:
        return
    pid, kind = int(parts[2]), parts[3]
    if kind not in KINDS:
        return
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        proposal = await session.get(RhythmProposal, pid)
        if not proposal or proposal.user_id != profile.id or proposal.status != 'pending':
            await callback.answer('Предложение уже закрыто.', show_alert=True)
            return
        proposal.kind = kind
        await session.commit()
        text, markup = proposal_text(proposal), proposal_markup(proposal)
    await callback.answer()
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith('rhythm:cancel:'))
async def cancel_proposal(callback: CallbackQuery) -> None:
    pid = int(callback.data.rsplit(':', 1)[-1])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        proposal = await session.get(RhythmProposal, pid)
        if not proposal or proposal.user_id != profile.id:
            await callback.answer('Не твоё предложение.', show_alert=True)
            return
        if proposal.status != 'pending':
            await callback.answer('Уже обработано.')
            return
        proposal.status = 'cancelled'
        await session.commit()
    await callback.answer()
    await callback.message.edit_text('Оставим просто разговором. Ничего не записано.')


@router.callback_query(F.data.startswith('rhythm:confirm:'))
async def confirm_proposal(callback: CallbackQuery) -> None:
    pid = int(callback.data.rsplit(':', 1)[-1])
    if pid in _confirming:
        await callback.answer('Уже сохраняю.')
        return
    _confirming.add(pid)
    try:
        try:
            async with SessionLocal() as session:
                profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
                proposal = await session.get(RhythmProposal, pid)
                if not proposal or proposal.user_id != profile.id or proposal.status != 'pending':
                    await callback.answer('Предложение уже обработано или недоступно.', show_alert=True)
                    return
                meal, lines = await record_confirmed_group(session, profile, proposal)
                await session.commit()
        except Exception as exc:
            print('[LIVING_RHYTHM] confirm failed:', repr(exc))
            await callback.answer('Не удалось записать; повтори попытку или открой обычный дневник.', show_alert=True)
            return
        await callback.answer('Записано')
        await callback.message.edit_text('✅ Записала одним приёмом пищи:\n' + '\n'.join(lines) +
                                         '\n\nРазберём весь приём вместе, а не каждую позицию отдельно.')
        await check_and_notify_challenges(callback.message, callback.from_user.id, callback.from_user.full_name)
    finally:
        _confirming.discard(pid)


@router.callback_query(F.data.startswith('rhythm:merge:'))
async def merge_callback(callback: CallbackQuery) -> None:
    parts = callback.data.split(':')
    if len(parts) != 5:
        return
    earlier, later, decision = int(parts[2]), int(parts[3]), parts[4]
    if decision != 'yes':
        await callback.answer()
        await callback.message.edit_text('Хорошо, оставим отдельными записями.')
        return
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        changed = await merge_group(session, profile.id, earlier, later)
        await session.commit()
    await callback.answer('Объединено' if changed else 'Уже недоступно')
    await callback.message.edit_text('✅ Сохранила как один приём пищи. КБЖУ не удвоены.' if changed else
                                     'Не получилось объединить; сами записи и награды не изменены.')


@router.callback_query(F.data.startswith('rhythm:treat:'))
async def treat_context(callback: CallbackQuery) -> None:
    choice = callback.data.rsplit(':', 1)[-1]
    if choice not in {'travel', 'long_gap', 'pleasure'}:
        return
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        if choice == 'travel':
            await append_event(session, profile.id, 'travel')
        elif choice == 'pleasure':
            await append_event(session, profile.id, 'choice')
        await session.commit()
    texts = {
        'travel': 'Поняла, сегодня ты в дороге. Если получится, найди вариант нормальной сытной еды, который удобно взять с собой.',
        'long_gap': 'Тогда полезнее подумать о полноценной еде, чем пытаться компенсировать день запретами.',
        'pleasure': 'Тоже нормальная причина. Не будем искать проблему там, где её нет.',
    }
    await callback.answer()
    await callback.message.edit_text('— ' + texts[choice])

@router.callback_query(F.data.startswith('rhythm:routine:'))
async def confirm_routine(callback: CallbackQuery) -> None:
    parts = callback.data.split(':')
    if len(parts) != 4 or parts[3] not in {'yes', 'different', 'later'}:
        return
    notification_id, decision = int(parts[2]), parts[3]
    try:
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
            note = await session.get(__import__('app.models', fromlist=['Notification']).Notification, notification_id)
            if (not note or note.user_id != profile.id or note.kind != 'rhythm_breakfast'
                or str((note.payload or {}).get('status')) != 'open'
                or aware(note.scheduled_at).date() != now_local().date()):
                await callback.answer('Это напоминание уже закрыто.', show_alert=True)
                return
            payload = dict(note.payload or {})
            if decision == 'yes':
                ids = list(payload.get('recipe_ids') or [])
                if not ids or len(ids) > 6:
                    await callback.answer('Для этого завтрака не сохранился набор рецептов.', show_alert=True)
                    return
                from app.models import SavedRecipe
                items = []
                for rid in ids:
                    recipe = await session.get(SavedRecipe, int(rid))
                    if recipe is None or recipe.user_id != profile.id:
                        await callback.answer('Один из рецептов удалён. Внеси завтрак обычным способом.', show_alert=True)
                        return
                    items.append({'name': recipe.name, 'category': recipe.category, 'quantity': 1,
                                  'recipe_id': recipe.id, 'source': 'recipe',
                                  'calories_kcal': recipe.calories_kcal, 'confidence': recipe.confidence})
                proposal = await create_proposal(session, profile.id, 'breakfast', items)
                meal, lines = await record_confirmed_group(session, profile, proposal)
                message = '✅ Завтрак записан одним действием:\n' + '\n'.join(lines)
            elif decision == 'different':
                message = '— Расскажи, что было сегодня. Например: «Съела овсянку и выпила кофе». Я сначала покажу запись для подтверждения.'
            else:
                message = '— Хорошо. Тогда не отвлекаю. Когда позавтракаешь, просто расскажи.'
            note.payload = {**payload, 'status': decision}
            await session.commit()
    except Exception as exc:
        print('[LIVING_RHYTHM] routine callback failed:', repr(exc))
        await callback.answer('Не смогла обработать, попробуй записать вручную.', show_alert=True)
        return
    await callback.answer('Записано' if decision == 'yes' else 'Поняла')
    await callback.message.edit_text(message)
    if decision == 'yes':
        await check_and_notify_challenges(callback.message, callback.from_user.id, callback.from_user.full_name)

@router.callback_query(F.data == 'rhythm:settings')
async def rhythm_settings_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_settings(callback.message, callback.from_user.id)

async def maybe_offer_training_skip(message: Message, actor, parsed: dict) -> None:
    """A missed training is a proposal, not an automatic penalty or skipped reward."""
    if not parsed.get('skip_training_today') or now_local().weekday() not in {0, 3}:
        return
    from app.models import Notification
    from sqlalchemy import select
    today = now_local().date()
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, actor.id, actor.full_name)
        try:
            from app.services.activity_breaks import is_paused
            if await is_paused(session, profile.id, today):
                return  # Already accommodated by the previous pause patch.
        except ImportError:
            pass
        key = f'rhythm:{profile.id}:{today}:skip_workout'
        exists = await session.scalar(select(Notification).where(Notification.dedupe_key == key))
        if exists:
            return
        note = Notification(user_id=profile.id, kind='rhythm_skip_workout',
                            scheduled_at=now_local(), sent_at=now_local(),
                            dedupe_key=key, payload={'status': 'open'})
        session.add(note)
        await session.flush()
        note_id = note.id
        await append_event(session, profile.id, 'schedule_change')
        await session.commit()
    await message.answer(
        '— Поняла, сегодня на тренировку не попадаешь. Отключить оставшиеся напоминания о ней на сегодня? '
        'Это не меняет награды или результаты прошлых тренировок.',
        reply_markup=kb([[('✅ Да, на сегодня', f'rhythm:skipworkout:{note_id}:yes')],
                         [('Нет, оставь напоминания', f'rhythm:skipworkout:{note_id}:no')]])
    )


@router.callback_query(F.data.startswith('rhythm:skipworkout:'))
async def skip_today_workout(callback: CallbackQuery) -> None:
    from sqlalchemy import select
    from app.models import Notification
    parts = callback.data.split(':')
    if len(parts) != 4 or parts[3] not in {'yes', 'no'}:
        return
    note_id, decision = int(parts[2]), parts[3]
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        note = await session.get(Notification, note_id)
        if not note or note.user_id != profile.id or note.kind != 'rhythm_skip_workout' or (note.payload or {}).get('status') != 'open':
            await callback.answer('Уже обработано.', show_alert=True)
            return
        if decision == 'yes':
            remaining = list((await session.scalars(select(Notification).where(
                Notification.user_id == profile.id,
                Notification.kind.in_(['monday_start', 'monday_finish', 'thursday_start', 'thursday_second']),
                Notification.sent_at.is_(None),
                Notification.scheduled_at >= now_local().replace(hour=0, minute=0, second=0, microsecond=0),
                Notification.scheduled_at < now_local().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1),
            ))).all())
            for reminder in remaining:
                reminder.sent_at = now_local()
        note.payload = {**dict(note.payload or {}), 'status': decision}
        await session.commit()
    await callback.answer()
    await callback.message.edit_text('— Хорошо, сегодня больше не зову в зал.' if decision == 'yes'
                                     else '— Ладно, расписание не меняю.')

@router.message(Command('meals'))
@router.message(F.text == '🗓 Приёмы пищи')
async def show_meal_diary(message: Message) -> None:
    from app.services.living_rhythm import today_meals_text
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        text = await today_meals_text(session, profile.id)
    await message.answer('🗓 Приёмы пищи сегодня\n\n' + text +
                         '\n\nНесколько позиций в одной строке — один приём пищи. КБЖУ складываются из подтверждённых позиций.')


@router.callback_query(F.data == 'rhythm:diary')
async def show_meal_diary_callback(callback: CallbackQuery) -> None:
    from app.services.living_rhythm import today_meals_text
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        text = await today_meals_text(session, profile.id)
    await callback.answer()
    await callback.message.answer('🗓 Приёмы пищи сегодня\n\n' + text)
