"""Validated actions invoked from a normal Selin conversation.

The assistant never writes directly: Python selects an owned record, applies the
change in one transaction, and only then reports success. Receipts dedupe auto
food logging across Telegram delivery retries without touching old data.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CompanionActionReceipt(Base):
    __tablename__ = 'selin_companion_action_receipts'
    __table_args__ = (UniqueConstraint('telegram_user_id', 'chat_id', 'message_id', name='uq_selin_action_receipt_message'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id', ondelete='CASCADE'), index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


async def action_receipt(session: AsyncSession, telegram_user_id: int, chat_id: int, message_id: int):
    return await session.scalar(select(CompanionActionReceipt).where(
        CompanionActionReceipt.telegram_user_id == telegram_user_id,
        CompanionActionReceipt.chat_id == chat_id,
        CompanionActionReceipt.message_id == message_id,
    ))


_EDIT = re.compile(r'(?:поменяй|поменять|измени|изменить|исправь|исправить|'
                   r'перенеси|перенести|отнеси|отнести|отметь|отмечено|'
                   r'записано|записала|всё\s+ещ[её]|все\s+ещ[её])', re.I)
_CATEGORY = re.compile(r'(?:категори\w*|\bкак\s+(?:перекус|еда|вкусняшк\w*|напиток)|'
                       r'(?:перекус|вкусняшк\w*)\b.*(?:вс[её]\s+ещ[её]|ошибочн))', re.I)
_FOOD_WORDS = re.compile(r'\b(?:йогурт\w*|кофе|леп[её]шк\w*|сыр\w*|десерт\w*|'
                         r'еда|перекус|вкусняшк\w*|завтрак|обед|ужин|напиток|'
                         r'банан\w*|батончик\w*|шоколад\w*)\b', re.I)
_STOP = {'поменяй','поменять','измени','изменить','исправь','исправить','селин','категорию',
         'категория','категории','категорией','пожалуйста','отметь','отмечено','отнеси',
         'перенеси','а','и','у','меня','мне','ты','это','его','ее','её','моя','мою','мой',
         'в','на','из','как','было','будет','теперь','сегодня','вчера','все','всё','еще','ещё',
         'полноценную','полноценная','полноценное','полноценный','прием','приём','пищи',
         'еда','еду','перекус','перекусом','вкусняшка','вкусняшку','вкусняшкой','десерт',
         'напиток','напитком','напитки','категориях','неправильно','ошибка','ошибочно','записано',
         'вместо','так','осталось','всеравно','равно','не','хочу','сделай',
         'калории','калорийность','калорий','ккал','поставь','цифры','цифру','порция'}


def category_request(text: str) -> bool:
    t = (text or '').casefold()
    return bool((_EDIT.search(t) and _CATEGORY.search(t)) or re.search(
        r'\b(?:перенеси|отнеси|поставь|отметь)\b.*\b(?:в|на|как)\s+(?:напит\w*|перекус\w*|полноценн\w*|вкусняшк\w*)', t))


def requested_category(text: str) -> str | None:
    t = (text or '').casefold().replace('ё','е')
    # Only explicit TARGET categories, not descriptions of the wrong current state.
    # 'как' is an instruction only after an imperative verb, not after 'отмечено как'.
    prefix = (r'(?:на|в|как|считай|сделай|отметь(?:\s+как)?)' if re.search(
        r'\b(?:поменяй|измени|исправь|отметь|перенеси|отнеси|сделай|считай)\b', t)
        else r'(?:на|в|считай|сделай|отметь(?:\s+как)?)')
    if re.search(prefix + r'\s+(?:полноценн\w*\s+)?(?:прием|ед[уаой]|основн\w*\s+блюд\w*)', t):
        return 'meal'
    if re.search(prefix + r'\s+(?:перекус\w*|снек)', t):
        return 'snack'
    if re.search(prefix + r'\s+(?:вкусняшк\w*|десерт\w*|сладост\w*)', t):
        return 'treat'
    if re.search(prefix + r'\s+(?:напит\w*|питье|кофе)', t):
        return 'drink'
    return None


def requested_calorie_fix(text: str) -> int | None:
    """Only explicit, single new calorie amounts; never guess old vs new."""
    t = (text or '').casefold()
    if not re.search(r'\b(?:исправь|поменяй|измени|поставь|скорректируй)\b', t):
        return None
    found = re.findall(r'\b(?:на|теперь|правильно|должно\s+быть)\s*(\d{1,4})\s*ккал\b', t)
    if len(found) != 1:
        return None
    value = int(found[0])
    return value if 0 <= value <= 5000 else None


def query_tokens(text: str) -> list[str]:
    t = (text or '').lower().replace('ё','е')
    t = re.sub(r'\b(?:на|в|как)\s+(?:полноценн\w*\s+)?(?:ед\w*|прием\s+пищи|перекус\w*|вкусняшк\w*|напит\w*|десерт\w*)\b.*$', ' ', t)
    words = re.findall(r'[а-яa-z]{4,}', t)
    return [w for w in words if w not in _STOP and not w.startswith(('категор','полноцен','отмеч','исправ','помен','запис','перенес','приём'))][:6]


def matching_records(rows: list, text: str) -> list:
    tokens = query_tokens(text)
    if not tokens:
        return rows[:8]
    ranked = []
    for row in rows:
        name = (row.description or '').lower().replace('ё','е')
        words = re.findall(r'[а-яa-z]{3,}', name)
        # Prefix match permits йогурта -> йогурт but excludes unrelated foods.
        score = sum(1 for token in tokens if any(a.startswith(token[:5]) or token.startswith(a[:5]) for a in words))
        if score:
            ranked.append((score, row))
    if not ranked:
        return []
    highest = max(v for v, _ in ranked)
    return [row for v, row in ranked if v == highest][:8]


def guard_unverified_claim(user_text: str, generated: str) -> str:
    """A language model may discuss actions, never claim an unsent DB commit."""
    t = (user_text or '').lower()
    # Includes non-command messages about food: "Кофе 3-в-1" is not an
    # explicit log operation, and the model must not tell the user it saved it.
    food_context = re.search(
        r'(?:помен|измен|исправ|удал|убер|запиш|отмет|перенес|еда|йогурт|кофе|'
        r'запис|перекус|категор|блюд|порц|ккал|дневник|съела|выпила|завтрак|обед)', t)
    if not food_context:
        return generated
    # Claim of completed action is untrusted unless the Python handler commits.
    claim = re.search(r'\b(?:я\s+)?(?:поменяла|изменила|исправила|удалила|убрала|'
                      r'записала|сохранила|перенесла)\b', generated.lower())
    if claim:
        return ('— Я пока не могу подтвердить изменение дневника. '
                'Если хочешь исправить или добавить запись, скажи, какую именно, '
                'и я выполню это через журнал либо предложу подтверждение.')
    return generated
