"""Telegram UI for the living item shop. Every purchase is a deliberate callback."""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.db import SessionLocal
from app.models import UserProfile
from app.services.ai_features import reserve_ai_call
from app.services.ai_engine import ai_enabled
from app.services.typing_status import while_typing
from app.services.rewards import get_or_create_profile
from app.services.wandering_shop import (
    OwnedWorldItem, ShopOffer, current_offers, get_state, install_rotation, inventory,
    inventory_context, now_local, purchase, recent_names, reroll_cost, save_reaction, use_item,
)
from app.services.wandering_shop_ai import make_catalog, make_reaction

router = Router()
_pending: set[int] = set()


def buttons(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        for row in rows
    ])


RECIPIENT = {'selin': 'Селин', 'tori': 'Тори', 'camp': 'убежища', 'party': 'путешествий'}
RARITY = {'common': 'обычный', 'uncommon': 'необычный', 'rare': 'редкий'}


async def render_shop(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, user_id, None)
        offers = await current_offers(session, profile.id)
        state = await get_state(session, profile.id)
        balance = int(profile.coins or 0)
        owned = list((await session.scalars(select(OwnedWorldItem.offer_id).where(
            OwnedWorldItem.user_id == profile.id,
        ))).all())
        await session.commit()
    bought = set(owned)
    lines = [f'🏮 Лавка странствий\n\nМонеты: {balance} 🪙',
             'Вещи текстовые: картинки генерировать не нужно.', '']
    rows = []
    for item in offers:
        lines.append(f'{item.slot + 1}. {item.name} · {item.price} 🪙 · {RARITY.get(item.rarity)}')
        lines.append(f'   Для {RECIPIENT.get(item.recipient, "отряда")}: {item.description}')
        label = f'✓ {item.slot + 1}. Куплено' if item.id in bought else f'🛍 {item.slot + 1}. {item.price} 🪙'
        rows.append([(label, f'wander:buy:{item.id}')])
    if not offers:
        lines.append('Пока полки пусты. Попроси торговца показать товары.')
        rows = [[('✨ Открыть лавку', 'wander:initial')]]
    else:
        cost = reroll_cost(state)
        line = f'🔄 Обновить ассортимент ({cost} 🪙)' if cost else '🔄 Обновить ассортимент бесплатно'
        rows.append([(line, 'wander:reroll')])
    rows.extend([[('🎒 Мои вещи', 'wander:inventory')], [('⬅️ Гардероб', 'wardrobe:menu')]])
    return '\n'.join(lines)[:3800], buttons(rows)


async def narrate_purchase_or_use(item, kind: str, user_id: int) -> str:
    # A reaction is optional: no AI budget means a local, text-only fallback.
    async with SessionLocal() as session:
        allowed = ai_enabled() and await reserve_ai_call(session, user_id, 'initiative')
        await session.commit()
    return await make_reaction(item, kind, ai_allowed=allowed)


async def send_shop(message: Message, user_id: int) -> None:
    text, markup = await render_shop(user_id)
    await message.answer(text, reply_markup=markup)


async def respond(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception:
        await callback.message.answer(text, reply_markup=markup)


@router.message(Command('shop', 'lavka'))
@router.message(F.text == '🏮 Лавка странствий')
async def shop_by_command(message: Message) -> None:
    # A first-time AI catalog can take a while. Acknowledge before waiting.
    progress = await message.answer('🏮 Торговец готовит ассортимент…')
    try:
        await while_typing(message, ensure_shop(message.from_user.id, message.from_user.full_name, initial_only=True))
        text, markup = await render_shop(message.from_user.id)
    except Exception:
        logger.exception('Shop opening failed for user_id=%s', message.from_user.id)
        text = ('Не удалось открыть лавку. Монеты за открытие не списываются.\n'
                'Напиши /shopcheck: он проверит доступность таблиц магазина. '
                'Если там ошибка, пришли её текст.')
        try:
            await progress.edit_text(text)
        except Exception:
            await message.answer(text)
        return
    try:
        await progress.edit_text(text, reply_markup=markup)
    except Exception:
        logger.exception('Could not edit shop placeholder; sending a fresh message')
        await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == 'wander:open')
@router.callback_query(F.data == 'wander:initial')
async def open_shop(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        await while_typing(callback.message, ensure_shop(
            callback.from_user.id, callback.from_user.full_name, initial_only=True))
    except Exception as exc:
        await callback.message.answer(f'Не удалось открыть лавку: {str(exc)[:180]}')
        return
    text, markup = await render_shop(callback.from_user.id)
    await respond(callback, text, markup)


async def ensure_shop(telegram_id: int, display_name: str, *, initial_only: bool = False,
                      refresh: bool = False) -> tuple[bool, int]:
    if telegram_id in _pending:
        raise ValueError('Торговец уже подбирает ассортимент. Открой лавку чуть позже.')
    _pending.add(telegram_id)
    try:
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, telegram_id, display_name)
            state = await get_state(session, profile.id)
            current = int(state.rotation or 0) if state else 0
            if initial_only and current:
                await session.commit()
                return False, 0
            if refresh and not current:
                refresh = False
            if not refresh and current:
                await session.commit()
                return False, 0
            names = await recent_names(session, profile.id)
            ai_allowed = ai_enabled() and await reserve_ai_call(session, profile.id, 'initiative')
            await session.commit()
        stock, generated = await make_catalog(names, seed=(telegram_id + current * 13 + now_local().date().toordinal()),
                                              ai_allowed=ai_allowed)
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, telegram_id, display_name)
            cost = await install_rotation(session, profile, stock,
                                          expected_rotation=current, reroll=refresh)
            await session.commit()
        return generated, cost
    finally:
        _pending.discard(telegram_id)


@router.callback_query(F.data == 'wander:reroll')
async def confirm_reroll(callback: CallbackQuery) -> None:
    await callback.answer()
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        state = await get_state(session, profile.id)
        cost = reroll_cost(state)
        await session.commit()
    await respond(callback, (
        '🔄 Обновить все шесть предложений торговца?\n'
        'Текущие товары пропадут с витрины, но купленные вещи останутся навсегда.\n'
        f'Стоимость обновления: {cost} 🪙.\n\n'
        'Генерация новых рисунков не нужна.'
    ), buttons([[('✅ Обновить', 'wander:reroll:go')], [('⬅️ Отмена', 'wander:open')]]))


@router.callback_query(F.data == 'wander:reroll:go')
async def reroll(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        generated, cost = await while_typing(callback.message, ensure_shop(
            callback.from_user.id, callback.from_user.full_name, refresh=True))
    except ValueError as exc:
        await callback.message.answer(str(exc))
        return
    except Exception as exc:
        print('[WANDER_SHOP] refresh error:', repr(exc))
        await callback.message.answer('Торговец пока не смог обновить полки. Монеты не потрачены.')
        return
    text, markup = await render_shop(callback.from_user.id)
    prefix = '✨ Новая партия товаров!\n' + ('' if generated else 'Сегодня торговец выбрал вещи из своих запасов.\n')
    await respond(callback, prefix + (f'Списано {cost} 🪙.\n\n' if cost else '\n') + text, markup)


@router.callback_query(F.data.startswith('wander:buy:'))
async def buy_item(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        offer_id = int(callback.data.rsplit(':', 1)[-1])
    except Exception:
        await callback.message.answer('Неизвестный товар.')
        return
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        try:
            item = await purchase(session, profile, offer_id)
            item_id, item_name, recipient, price = item.id, item.name, item.recipient, (await session.get(ShopOffer, offer_id)).price
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            await callback.message.answer(str(exc))
            return
        except Exception as exc:
            await session.rollback()
            print('[WANDER_SHOP] purchase:', repr(exc))
            await callback.message.answer('Не удалось завершить покупку. Проверь лавку и баланс.')
            return
    # Narrative failure cannot undo a successful purchase or charge twice.
    reaction = await while_typing(callback.message, narrate_purchase_or_use(item, 'purchase', profile.id))
    try:
        async with SessionLocal() as session:
            await save_reaction(session, profile.id, item_id, reaction)
            await session.commit()
    except Exception as exc:
        print('[WANDER_SHOP] could not store reaction:', repr(exc))
    await callback.message.answer(
        f'🎁 Получено: {item_name} · −{price} 🪙\nДля {RECIPIENT.get(recipient, "отряда")}.\n\n{reaction}',
        reply_markup=buttons([[('🎒 Вещь в инвентаре', f'wander:item:{item_id}')],
                              [('🏮 Обратно в лавку', 'wander:open')]]),
    )


async def inventory_view(user_id: int, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, user_id, None)
        # Limit to recent 60, with stable pagination of 6
        items = await inventory(session, profile.id, limit=60)
        await session.commit()
    page = min(max(page, 0), max(0, (len(items) - 1) // 6))
    shown = items[page * 6:(page + 1) * 6]
    lines = ['🎒 Инвентарь странствий', f'Предметов: {len(items)}' + (' (последние 60)' if len(items) >= 60 else ''), '']
    rows = []
    for item in shown:
        lines.append(f'• {item.name} · {RECIPIENT.get(item.recipient, "отряда")} · {item.status}')
        rows.append([(f'✨ {item.name[:27]}', f'wander:item:{item.id}')])
    if not shown:
        lines.append('Пока пусто. Вещи появятся после первых покупок.')
    page_buttons = []
    if page > 0:
        page_buttons.append(('⬅️', f'wander:inventory:{page - 1}'))
    if (page + 1) * 6 < len(items):
        page_buttons.append(('➡️', f'wander:inventory:{page + 1}'))
    if page_buttons:
        rows.append(page_buttons)
    rows.append([('🏮 Лавка', 'wander:open')])
    return '\n'.join(lines), buttons(rows)


@router.callback_query(F.data.startswith('wander:inventory'))
async def view_inventory(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        page = int(callback.data.split(':')[2]) if len(callback.data.split(':')) > 2 else 0
    except ValueError:
        page = 0
    text, markup = await inventory_view(callback.from_user.id, page)
    await respond(callback, text, markup)


@router.callback_query(F.data.startswith('wander:item:'))
async def view_item(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        item_id = int(callback.data.rsplit(':', 1)[-1])
    except ValueError:
        return
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        item = await session.scalar(select(OwnedWorldItem).where(
            OwnedWorldItem.id == item_id, OwnedWorldItem.user_id == profile.id))
        if item is None:
            await callback.message.answer('Такой вещи в твоём инвентаре нет.')
            return
        name, desc, hint, uses, status, reaction = item.name, item.description, item.use_hint, item.uses, item.status, item.last_reaction
    await respond(callback,
        f'🎒 {name}\n{desc}\n\nГде: {status}.\nКак может пригодиться: {hint}\n'
        f'Использований: {uses}\n\nПоследнее событие: {reaction[:400] or "Пока нет."}',
        buttons([[('✨ Использовать в сцене', f'wander:use:{item_id}')],
                 [('⬅️ Инвентарь', 'wander:inventory')]]))


@router.callback_query(F.data.startswith('wander:use:'))
async def use(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        item_id = int(callback.data.rsplit(':', 1)[-1])
    except ValueError:
        return
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        try:
            item = await use_item(session, profile, item_id)
            await session.commit()
        except ValueError as exc:
            await callback.message.answer(str(exc))
            return
    reaction = await while_typing(callback.message, narrate_purchase_or_use(item, 'use', profile.id))
    async with SessionLocal() as session:
        await save_reaction(session, profile.id, item_id, reaction, event_type='scene')
        await session.commit()
    await callback.message.answer(reaction,
        reply_markup=buttons([[('🎒 Посмотреть вещь', f'wander:item:{item_id}')]]))
