"""Selin's real tool-calling agent. Model chooses tools; Python owns every mutation.

No keyword-based intent dispatcher, implicit payment, or fabricated DB success.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, select, update
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, SessionLocal
from app.models import DailyStat, FoodNutritionLog, SavedRecipe, UserProfile
from app.services.ai_engine import CHAT_SCHEMA, _client, _selin_system
from app.services.ai_features import now_local
from app.services.rewards import get_or_create_profile
from app.services.food_corrections import (
    apply_correction, checked_log, describe, recent_logs, signature,
)
from app.services.nutrition import daily_nutrition_totals
from app.services.activity_breaks import (
    start_break, resume_break, get_break, is_in_effect, adapt_today_challenges,
)
from app.services.wandering_shop import (
    OwnedWorldItem, current_offers, get_state, inventory, purchase, use_item,
    install_rotation, recent_names, reroll_cost,
)
from app.services.living_rhythm import (
    create_proposal, record_confirmed_group, get_settings, KINDS, RhythmProposal,
)
from app.config import settings

log = logging.getLogger('selin.agent')


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentPendingAction(Base):
    __tablename__ = 'selin_agent_pending_actions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id', ondelete='CASCADE'), index=True)
    kind: Mapped[str] = mapped_column(String(45))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    label: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentActionReceipt(Base):
    __tablename__ = 'selin_agent_action_receipts'
    __table_args__ = (UniqueConstraint('user_id', 'message_key', 'action_key', name='uq_selin_agent_action'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id', ondelete='CASCADE'), index=True)
    message_key: Mapped[str] = mapped_column(String(120))
    action_key: Mapped[str] = mapped_column(String(130))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _function(name: str, description: str, props: dict, required: list[str] | None = None) -> dict:
    return {
        'type': 'function', 'name': name, 'description': description, 'strict': True,
        'parameters': {'type': 'object', 'properties': props, 'required': required or list(props), 'additionalProperties': False},
    }


def S(desc: str = '') -> dict:
    return {'type':'string', 'description':desc}


def I(desc: str = '') -> dict:
    return {'type':'integer', 'description':desc}


def N(desc: str = '') -> dict:
    return {'type':'number', 'description':desc}


TOOLS = [
    _function('find_food', 'Посмотреть РЕАЛЬНЫЕ записи еды и ID для исправления/удаления; сначала используй этот поиск.', {'query': S('Имя/часть имени; пустая строка = последние записи')}),
    _function('get_today', 'Фактические КБЖУ и дневные счётчики из базы.', {}),
    _function('record_steps', 'Записать объявленное пользователем КОЛИЧЕСТВО шагов за сегодня. Не выдумывай число.', {'steps':I('Реально пройденные шаги 0..100000')}),
    _function('edit_food', 'Изменить одно поле конкретной найденной записи еды. Только если желаемое новое значение однозначно известно.', {'id': I('ID из find_food'), 'field': {'type':'string','enum':['category','description','calories_kcal','protein_g','fat_g','carbs_g','portion_grams']}, 'value': S('Новое значение. Для category: meal/snack/treat/drink. Для чисел десятичная точка.')}),
    _function('delete_food', 'Предложить удаление найденной записи с Telegram-подтверждением. Никогда не удаляй молча.', {'id': I('ID из find_food')}),
    _function('find_recipes', 'Список сохранённых рецептов с точными ID и КБЖУ. Нужен перед записью съеденного рецепта.', {'query': S('Название или пустая строка для последних рецептов')}),
    _function('log_recipes', 'Записать уже съеденные сохранённые рецепты одним приёмом. Только по явному факту еды/напитка и известным порциям.', {
        'recipes': {'type':'array','items':{'type':'object','properties':{'id':I('ID найденного рецепта'), 'quantity':N('Число порций от 0.1 до 5')},'required':['id','quantity'],'additionalProperties':False}},
        'meal_kind': {'type':'string','enum':['breakfast','lunch','dinner','snack','unknown']},
    }),
    _function('estimate_new_food', 'Если нет сохранённого рецепта, оцени неизвестную еду и предложи запись с обязательным подтверждением. Не используй для желания или планов.', {'description':S('Состав, размер порции, фактически съедено/выпито'), 'category':{'type':'string','enum':['meal','snack','treat','drink']}, 'meal_kind': {'type':'string','enum':['breakfast','lunch','dinner','snack','unknown']}}),
    _function('training_status', 'Узнать реально активную паузу тренировок.', {}),
    _function('pause_training', 'Предложить поставить спорт на паузу. Требует подтверждения кнопкой.', {'days': I('Количество дней от 1 до 90'), 'reason':{'type':'string','enum':['tattoo','rest','illness','other']}}),
    _function('resume_training', 'Предложить досрочное возобновление тренировок, с подтверждением.', {}),
    _function('get_shop', 'Узнать реальные предложения лавки, ID, цены, остаток монет.', {}),
    _function('refresh_shop', 'Открыть/обновить лавку; если обновление стоит монет, спросит подтверждение.', {}),
    _function('get_inventory', 'Посмотреть настоящие купленные вещи, ID, получателей и использования.', {}),
    _function('buy_item', 'Предложить покупку найденного товара за монеты; обязательно спросит подтверждение.', {'offer_id':I('ID товара из get_shop')}),
    _function('use_item', 'Реально разыграть использование купленной вещи; сохранить применение и реакцию персонажа для следующих сцен.', {'item_id':I('ID из get_inventory')}),
    _function('set_initiative', 'Изменить частоту инициативных сообщений Селин по прямой просьбе пользователя.', {'mode':{'type':'string','enum':['calm','normal','caring']}}),
]


AGENT_RULES = '''\nТЫ ИМЕЕШЬ НАСТОЯЩИЕ ИНСТРУМЕНТЫ. Это единственный способ читать или менять данные в игре.
Всё общение — свободный чат; сама вызывай инструменты, когда они нужны. Не проси пользователя переходить в меню, если инструмент может помочь.
Для записи/редактирования/покупки используй реальный ID, полученный через find_food/find_recipes/get_shop/get_inventory в ЭТОМ запросе.
Если запись неоднозначна, спроси уточнение. Никаких догадок об ID или точных калориях.
Однозначные исправления еды делай сразу, но удаление, покупки, траты монет и спортивная пауза требуют кнопки подтверждения.
«Хочу», «может быть», «позже» — не означает «съела». Не записывай желания как съеденное.
Для напитков с непонятным рецептом сначала выясни состав; не выдумывай точное КБЖУ.
Твои инструменты сообщат truth-status: success, pending, error. NEVER объявляй успех, пока success не получен.
Когда pending, кратко попроси нажать кнопку в Telegram. Не утверждай, будто действие уже выполнено.
В одном сообщении может быть несколько независимых просьб, обрабатывай их последовательно.
Не поощряй ограничения питания и отказ от еды, не назначай медицинские запреты.
Если пользователь просто болтает, отвечай как Селин и не навязывай инструменты/анализ.
'''


def _food_dict(row: FoodNutritionLog) -> dict:
    return {'id':row.id,'date':str(row.logged_on),'name':row.description,'category':row.category,
            'portion':row.portion_label,'grams':row.portion_grams,'calories_kcal':row.calories_kcal,
            'protein_g':row.protein_g,'fat_g':row.fat_g,'carbs_g':row.carbs_g}


def _recipe_dict(r: SavedRecipe) -> dict:
    return {'id':r.id,'name':r.name,'category':r.category,'kcal':r.calories_kcal,
            'protein_g':r.protein_g,'fat_g':r.fat_g,'carbs_g':r.carbs_g,'portion_grams':r.portion_grams}


def _offer_dict(o) -> dict:
    return {'id':o.id,'name':o.name,'description':o.description,'price':o.price,'recipient':o.recipient}


async def _pending(session, user_id: int, kind: str, args: dict, label: str) -> AgentPendingAction:
    row = AgentPendingAction(user_id=user_id, kind=kind, arguments=args,
                             label=label[:500], expires_at=_utc_now() + timedelta(hours=2))
    session.add(row)
    await session.flush()
    return row


async def execute_pending(user_id: int, action_id: int, confirm: bool) -> str:
    """One atomic transaction for approval AND side effect; always scoped to owner."""
    async with SessionLocal() as session:
        row = await session.scalar(select(AgentPendingAction).where(
            AgentPendingAction.user_id == user_id, AgentPendingAction.id == action_id).with_for_update())
        if not row or row.status != 'pending' or _aware_utc(row.expires_at) < _utc_now():
            return 'Действие уже обработано или срок подтверждения истёк.'
        claimed = await session.execute(update(AgentPendingAction).where(
            AgentPendingAction.id == row.id, AgentPendingAction.status == 'pending').values(status='processing'))
        if claimed.rowcount != 1:
            return 'Действие уже обрабатывается.'
        if not confirm:
            row.status = 'declined'
            await session.commit()
            return 'Отменила. Ничего не меняла.'
        profile = await session.get(UserProfile, user_id)
        if profile is None:
            raise ValueError('Не нашла игровой профиль.')
        a = dict(row.arguments or {})
        if row.kind == 'delete_food':
            food = await checked_log(session, user_id, int(a['id']))
            if food is None or signature(food) != a['signature']:
                raise ValueError('Запись уже изменилась; попроси найти её снова.')
            await apply_correction(session, user_id, food.id, a['signature'], delete=True)
            result = f'Удалила запись «{a["name"]}» из дневника.'
        elif row.kind == 'pause_training':
            days = int(a['days'])
            if not 1 <= days <= 90: raise ValueError('Неверный срок')
            br = await start_break(session, user_id, days, reason=a['reason'])
            await adapt_today_challenges(session, user_id, now_local().date())
            result = f'Спортивная пауза включена до {br.check_on:%d.%m.%Y}; прогулки остаются.'
        elif row.kind == 'resume_training':
            changed = await resume_break(session, user_id)
            result = 'Спортивная пауза снята.' if changed else 'Пауза уже была снята.'
        elif row.kind == 'buy_item':
            # Recheck price and rotation on confirm; purchase() owns the atomic debit.
            offer_id = int(a['offer_id'])
            offers = await current_offers(session, user_id)
            offer = next((o for o in offers if o.id == offer_id), None)
            if offer is None or int(offer.price) != int(a['price']):
                raise ValueError('Товар или цена поменялись; открой лавку снова.')
            item = await purchase(session, profile, offer_id)
            result = f'Купила «{item.name}» за {offer.price} монет. Вещь сохранена в инвентаре.'
        elif row.kind == 'refresh_shop':
            from app.services.wandering_shop_ai import make_catalog
            from app.services.ai_engine import ai_enabled
            state = await get_state(session, user_id)
            cost = reroll_cost(state)
            if cost != int(a['cost']):
                raise ValueError('Цена обновления изменилась, запроси заново.')
            seed = int(a['rotation']) + 1
            items, _ = await make_catalog(await recent_names(session, user_id), seed=seed, ai_allowed=ai_enabled())
            await install_rotation(session, profile, items, expected_rotation=int(a['rotation']), reroll=bool(a['rotation']))
            result = 'Ассортимент обновлён. Новые вещи уже в лавке.'
        elif row.kind == 'log_new_food':
            proposal = await session.get(RhythmProposal, int(a['proposal_id']))
            if not proposal or proposal.user_id != user_id or proposal.status != 'pending':
                raise ValueError('Предложение еды уже устарело.')
            _, lines = await record_confirmed_group(session, profile, proposal)
            result = 'Записала с приблизительной оценкой:\n'+'\n'.join(lines)
        else:
            raise ValueError('Неизвестное действие')
        row.status = 'done'
        await session.commit()
        if row.kind == 'buy_item':
            try:
                from app.services.wandering_shop_ai import make_reaction
                from app.services.wandering_shop import save_reaction
                reaction = await make_reaction(item, 'buy')
                async with SessionLocal() as reaction_session:
                    await save_reaction(reaction_session, user_id, item.id, reaction, event_type='reaction')
                    await reaction_session.commit()
                result += '\n\n' + reaction
            except Exception:
                log.exception('Purchase committed, but character reaction failed')
                result += '\nРеакция пока не загрузилась, но вещь в инвентаре.'
        return '✅ ' + result


class AgentTurn:
    def __init__(self, user_id: int, message_key: str):
        self.user_id = user_id
        self.message_key = message_key
        self.seen_food: set[int] = set()
        self.seen_recipes: set[int] = set()
        self.seen_offers: set[int] = set()
        self.seen_items: set[int] = set()
        self.pending: list[tuple[int, str]] = []
        self.completed: list[str] = []
        self.performed: dict[str, dict] = {}

    async def execute(self, name: str, a: dict) -> dict:
        if name not in {t['name'] for t in TOOLS}:
            return {'status':'error','reason':'Неизвестный инструмент'}
        try:
            return await self._execute(name, a)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            return {'status':'error','reason':str(exc)[:300]}
        except Exception:
            log.exception('Agent tool failed: %s', name)
            return {'status':'error','reason':'Сбой исполнения в базе. Ничего не подтверждено.'}

    async def _execute(self, name: str, a: dict) -> dict:
        async with SessionLocal() as session:
            profile = await session.get(UserProfile, self.user_id)
            if not profile:
                raise ValueError('Профиль не найден')
            if name == 'find_food':
                query = str(a['query']).strip().casefold()
                rows = await recent_logs(session, self.user_id, 70)
                rows = [r for r in rows if query in r.description.casefold()][:12]
                self.seen_food.update(r.id for r in rows)
                return {'status':'success','records':[_food_dict(r) for r in rows]}
            if name == 'get_today':
                today = now_local().date()
                stat = await session.scalar(select(DailyStat).where(DailyStat.user_id==self.user_id, DailyStat.stat_date==today))
                totals = await daily_nutrition_totals(session, self.user_id, today)
                return {'status':'success','date':str(today),'totals':totals,'steps':stat.steps if stat else 0,
                        'meals':stat.meals if stat else 0,'snacks':stat.snacks if stat else 0}
            if name == 'record_steps':
                value=int(a['steps'])
                if not 0 <= value <= 100000: raise ValueError('Количество шагов должно быть от 0 до 100000.')
                action_key='record_steps'
                receipt=await session.scalar(select(AgentActionReceipt).where(
                    AgentActionReceipt.user_id==self.user_id, AgentActionReceipt.message_key==self.message_key,
                    AgentActionReceipt.action_key==action_key))
                if receipt:
                    return {'status':'success','message':'Это число шагов уже обработано по твоему сообщению.'}
                from app.services.rewards import get_or_create_daily, apply_reward
                from app.services.steps import evaluate_steps
                from app.services.achievements import check_achievements
                stat=await get_or_create_daily(session,self.user_id,now_local().date())
                tier=evaluate_steps(value)
                xp_delta=max(0,tier.xp-(stat.step_reward_xp or 0))
                coin_delta=max(0,tier.coins-(stat.step_reward_coins or 0))
                stat.steps=value
                stat.step_tier=tier.key
                stat.step_reward_xp=max(stat.step_reward_xp or 0,tier.xp)
                stat.step_reward_coins=max(stat.step_reward_coins or 0,tier.coins)
                if xp_delta or coin_delta:
                    await apply_reward(session,profile,event_type='steps_update',xp=xp_delta,coins=coin_delta,
                                       payload={'steps':value,'tier':tier.key})
                await check_achievements(session,profile,steps=value)
                session.add(AgentActionReceipt(user_id=self.user_id,message_key=self.message_key,
                                               action_key=action_key,result={'steps':value}))
                await session.commit()
                text=f'Шаги сегодня: {value}. Награда: +{xp_delta} XP, +{coin_delta} монет.'
                self.completed.append(text)
                return {'status':'success','message':text}
            if name == 'edit_food':
                rid, field, raw = int(a['id']), a['field'], str(a['value']).strip()
                if rid not in self.seen_food:
                    raise ValueError('Сначала найди запись через find_food.')
                row = await checked_log(session, self.user_id, rid)
                if not row: raise ValueError('Запись не найдена или слишком старая.')
                action_key=f'edit_food:{rid}:{field}'
                old_receipt = await session.scalar(select(AgentActionReceipt).where(
                    AgentActionReceipt.user_id == self.user_id,
                    AgentActionReceipt.message_key == self.message_key,
                    AgentActionReceipt.action_key == action_key))
                if old_receipt:
                    return {'status':'success','message':'Эту правку уже обработала, повторно не изменяю.', 'receipt':old_receipt.result}
                cats = {'meal','snack','treat','drink'}
                if field == 'category':
                    value = {'еда':'meal','основное':'meal','полноценная еда':'meal','завтрак':'meal','обед':'meal','ужин':'meal',
                             'перекус':'snack','вкусняшка':'treat','сладость':'treat','напиток':'drink'}.get(raw.casefold(),raw)
                    if value not in cats: raise ValueError('Неизвестная категория.')
                elif field == 'description':
                    if not 2 <= len(raw) <= 300: raise ValueError('Некорректное название.')
                    value = raw
                else:
                    value = float(raw.replace(',','.'))
                    if (field == 'portion_grams' and not 0 < value <= 5000) or (field != 'portion_grams' and not 0 <= value <= (5000 if field=='calories_kcal' else 1000)):
                        raise ValueError('Значение за пределами допустимого диапазона.')
                result, totals = await apply_correction(session, self.user_id, rid, signature(row), {field:value})
                session.add(AgentActionReceipt(user_id=self.user_id, message_key=self.message_key,
                                               action_key=action_key, result={'id':rid,'field':field,'value':value}))
                await session.commit()
                await session.refresh(result)
                if getattr(result,field) != value: raise ValueError('Не удалось проверить сохранение.')
                text = f'Исправила «{result.description}»: {field} = {value}. Сумма дня: {totals.get("calories_kcal", "?")} ккал.'
                self.completed.append(text)
                return {'status':'success','message':text,'record':_food_dict(result)}
            if name == 'delete_food':
                rid=int(a['id'])
                if rid not in self.seen_food: raise ValueError('Сначала найди запись через find_food.')
                row = await checked_log(session,self.user_id,rid)
                if not row: raise ValueError('Запись не найдена')
                item = await _pending(session,self.user_id,'delete_food',{'id':rid,'signature':signature(row),'name':row.description},
                                      f'Удалить «{row.description}» от {row.logged_on:%d.%m}?')
                await session.commit()
                self.pending.append((item.id,item.label))
                return {'status':'pending','action_id':item.id,'message':item.label}
            if name == 'find_recipes':
                query=str(a['query']).casefold().strip()
                rows=list((await session.scalars(select(SavedRecipe).where(SavedRecipe.user_id==self.user_id)
                     .order_by(SavedRecipe.is_favorite.desc(), SavedRecipe.uses.desc(),SavedRecipe.id.desc()).limit(80))).all())
                rows=[r for r in rows if query in r.name.casefold()][:16]
                self.seen_recipes.update(r.id for r in rows)
                return {'status':'success','recipes':[_recipe_dict(r) for r in rows]}
            if name == 'log_recipes':
                recipes = list(a['recipes'])
                kind = a['meal_kind']
                if not 1 <= len(recipes) <= 5 or kind not in KINDS: raise ValueError('Неверный состав приёма.')
                key = 'log_recipes'
                receipt = await session.scalar(select(AgentActionReceipt).where(
                    AgentActionReceipt.user_id==self.user_id,AgentActionReceipt.message_key==self.message_key,
                    AgentActionReceipt.action_key==key))
                if receipt:
                    return {'status':'success','message':'Этот приём уже записан, дублирования нет.', 'receipt':receipt.result}
                items=[]
                for item in recipes:
                    rid=int(item['id']); qty=float(item['quantity'])
                    if rid not in self.seen_recipes or not .1 <= qty <= 5:
                        raise ValueError('Сначала найди каждый рецепт и уточни порции.')
                    recipe=await session.get(SavedRecipe,rid)
                    if not recipe or recipe.user_id!=self.user_id: raise ValueError('Рецепт не найден')
                    items.append({'name':recipe.name,'category':recipe.category,'quantity':qty,'recipe_id':rid,
                                  'source':'recipe','nutrition':{}})
                proposal=await create_proposal(session,self.user_id,kind,items)
                meal,lines=await record_confirmed_group(session,profile,proposal)
                summary='Записала одним приёмом: '+'; '.join(lines)
                session.add(AgentActionReceipt(user_id=self.user_id,message_key=self.message_key,
                                               action_key=key,result={'meal_id':meal.id}))
                await session.commit()
                self.completed.append(summary)
                return {'status':'success','message':summary,'meal_id':meal.id}
            if name == 'estimate_new_food':
                desc=str(a['description']).strip()[:350]
                cat=a['category']; kind=a['meal_kind']
                if len(desc)<4 or cat not in {'meal','snack','treat','drink'} or kind not in KINDS:
                    raise ValueError('Недостаточно данных для оценки.')
                # Network analysis outside transaction. No real data mutation before confirmation.
                from app.services.ai_engine import analyze_food_text
                result = await analyze_food_text(desc)
                result['category']=cat
                props=[{'name':desc,'category':cat,'quantity':1,'source':'analysis','nutrition':result}]
                proposal=await create_proposal(session,self.user_id,kind,props)
                kcal=result.get('calories_kcal')
                label=f'Записать «{desc[:65]}», оценочно {kcal if kcal is not None else "?"} ккал?'
                action=await _pending(session,self.user_id,'log_new_food',{'proposal_id':proposal.id},label)
                await session.commit()
                self.pending.append((action.id,action.label))
                return {'status':'pending','message':label,'action_id':action.id,'estimate':result}
            if name == 'training_status':
                row=await get_break(session,self.user_id)
                return {'status':'success','active':is_in_effect(row,now_local().date()),
                        'until':str(row.check_on) if row else None,'reason':row.reason if row else None}
            if name == 'pause_training':
                days=int(a['days']); reason=a['reason']
                if not 1<=days<=90 or reason not in {'tattoo','rest','illness','other'}:
                    raise ValueError('Неверный период паузы.')
                current=await get_break(session,self.user_id)
                if is_in_effect(current,now_local().date()):
                    return {'status':'success','message':'Спортивная пауза уже активна до '+str(current.check_on)}
                label=f'Приостановить тренировки на {days} дн. ({reason})?'
                action=await _pending(session,self.user_id,'pause_training',{'days':days,'reason':reason},label)
                await session.commit()
                self.pending.append((action.id,label))
                return {'status':'pending','message':label,'action_id':action.id}
            if name == 'resume_training':
                row=await get_break(session,self.user_id)
                if not is_in_effect(row,now_local().date()):
                    return {'status':'success','message':'Спортивная пауза уже не активна.'}
                action=await _pending(session,self.user_id,'resume_training',{},'Возобновить спортивные напоминания и челленджи?')
                await session.commit()
                self.pending.append((action.id,action.label))
                return {'status':'pending','action_id':action.id,'message':action.label}
            if name == 'get_shop':
                offers=await current_offers(session,self.user_id)
                state=await get_state(session,self.user_id)
                self.seen_offers.update(x.id for x in offers)
                return {'status':'success','coins':int(profile.coins or 0),
                        'rotation':state.rotation if state else 0,'reroll_cost':reroll_cost(state),
                        'offers':[_offer_dict(x) for x in offers]}
            if name == 'refresh_shop':
                state=await get_state(session,self.user_id)
                rotation=int(state.rotation or 0) if state else 0
                cost=reroll_cost(state) if rotation else 0
                if cost:
                    action=await _pending(session,self.user_id,'refresh_shop',{'rotation':rotation,'cost':cost},f'Обновить ассортимент за {cost} монет?')
                    await session.commit()
                    self.pending.append((action.id,action.label))
                    return {'status':'pending','message':action.label,'action_id':action.id}
                from app.services.wandering_shop_ai import make_catalog
                from app.services.ai_engine import ai_enabled
                items, _ = await make_catalog(await recent_names(session, self.user_id), seed=rotation+1, ai_allowed=ai_enabled())
                await install_rotation(session,profile,items,expected_rotation=rotation,reroll=bool(rotation))
                await session.commit()
                return {'status':'success','message':'Новый ассортимент выставлен; смотри get_shop.'}
            if name == 'get_inventory':
                rows=await inventory(session,self.user_id,limit=25)
                self.seen_items.update(x.id for x in rows)
                return {'status':'success','items':[{'id':x.id,'name':x.name,'recipient':x.recipient,'status':x.status,
                      'uses':x.uses,'use_hint':x.use_hint,'last_scene':(x.last_reaction or '')[:350]} for x in rows]}
            if name == 'buy_item':
                rid=int(a['offer_id'])
                if rid not in self.seen_offers: raise ValueError('Сначала посмотри ассортимент через get_shop.')
                offers=await current_offers(session,self.user_id)
                offer=next((x for x in offers if x.id==rid),None)
                if not offer: raise ValueError('Товар уже недоступен.')
                if offer.price > int(profile.coins or 0):
                    return {'status':'error','reason':f'Недостаточно монет: {profile.coins}, нужно {offer.price}.'}
                action=await _pending(session,self.user_id,'buy_item',{'offer_id':offer.id,'price':offer.price},
                                      f'Купить «{offer.name}» за {offer.price} монет?')
                await session.commit()
                self.pending.append((action.id,action.label))
                return {'status':'pending','action_id':action.id,'message':action.label}
            if name == 'use_item':
                rid=int(a['item_id'])
                if rid not in self.seen_items: raise ValueError('Сначала найди вещь через get_inventory.')
                action_key=f'use_item:{rid}'
                receipt=await session.scalar(select(AgentActionReceipt).where(
                    AgentActionReceipt.user_id==self.user_id,AgentActionReceipt.message_key==self.message_key,
                    AgentActionReceipt.action_key==action_key))
                if receipt:
                    return {'status':'success','message':'Вещь уже использована по этому сообщению. Не повторяю.'}
                item=await use_item(session,profile,rid)
                session.add(AgentActionReceipt(user_id=self.user_id,message_key=self.message_key,
                                               action_key=action_key,result={'item_id':rid}))
                await session.commit()
                # The use has already committed, so AI/network errors cannot roll it back.
                # Generate a fresh reaction and retain it in the world inventory memory.
                story = ''
                try:
                    from app.services.wandering_shop_ai import make_reaction
                    from app.services.wandering_shop import save_reaction
                    from app.services.ai_engine import ai_enabled
                    if ai_enabled():
                        story = await make_reaction(item, 'use')
                        async with SessionLocal() as event_session:
                            await save_reaction(event_session, self.user_id, item.id, story,
                                                event_type='agent_use_scene')
                            await event_session.commit()
                except Exception:
                    log.exception('Item use was committed, but its narrative reaction failed')
                text=f'«{item.name}» использован, событие сохранено.'
                self.completed.append(text)
                return {'status':'success','message':text, 'scene':story,
                        'hint':item.use_hint}
            if name == 'set_initiative':
                mode=a['mode']
                if mode not in {'calm','normal','caring'}: raise ValueError('Неизвестный режим.')
                row=await get_settings(session,self.user_id)
                row.mode=mode
                await session.commit()
                text={'calm':'Спокойный','normal':'Обычный','caring':'Заботливый'}[mode]
                self.completed.append('Режим инициативы: '+text)
                return {'status':'success','message':'Режим инициативы установлен: '+text}
        return {'status':'error','reason':'Невыполнимый инструмент'}


async def agent_reply(*, user_text: str, context: dict, recent_chat: str, can_offer_quest: bool,
                      profile_id: int, message_key: str) -> tuple[dict, AgentTurn]:
    """Actual Responses function-call loop, max 5 tool rounds, with explicit results."""
    turn=AgentTurn(profile_id,message_key)
    system=_selin_system(context,can_offer_quest=can_offer_quest)
    system=system.replace("Даже если пользователь просит это сделать, только предложи действие и уточнение — не изображай успех. Операции выполняет Python.",
                          "Для настоящих операций вызови подходящий инструмент. Python выполнит изменение и вернёт проверенный результат.")
    system+='\n'+AGENT_RULES
    messages=[{'role':'system','content':system},
              {'role':'user','content':f'Недавний диалог:\n{recent_chat[-3500:]}\n\nНовое сообщение:\n{user_text}'}]
    client=_client()
    for iteration in range(6):
        try:
            response=await client.responses.create(
            model=settings.openai_model, input=messages, tools=TOOLS,
            parallel_tool_calls=False, max_output_tokens=2600,
            text={'format':{'type':'json_schema','name':'selin_agent_reply_v1',
                            'schema':CHAT_SCHEMA,'strict':True}},
        )
        except Exception:
            if turn.completed or turn.pending:
                log.exception('OpenAI failed after applying agent tools; returning verified results')
                return ({'text':'Я выполнила часть действий; остальное не подтверждено. Проверь итог ниже.',
                         'emotion':'neutral','memories_to_save':[], 'offer_quest':False}, turn)
            raise
        if response.status!='completed':
            if turn.completed or turn.pending:
                return ({'text':'Часть действий уже обработана; остальное не подтверждено.',
                         'emotion':'neutral','memories_to_save':[], 'offer_quest':False}, turn)
            raise RuntimeError(f'OpenAI response incomplete: {response.status} {response.incomplete_details}')
        calls=[item for item in response.output if item.type=='function_call']
        if not calls:
            try:
                data=json.loads(response.output_text or '')
                if not isinstance(data,dict): raise ValueError('Not an object')
            except (ValueError,TypeError):
                data={'text':str(response.output_text or 'Я не уверена, что поняла. Повтори, пожалуйста.'),
                      'emotion':'neutral','memories_to_save':[], 'offer_quest':False}
            return data,turn
        # Retain reasoning and tool call IDs, required for reasoning-class models.
        messages.extend(response.output)
        for call in calls:
            try:
                args=json.loads(call.arguments)
                if not isinstance(args,dict): raise ValueError('Args must be a JSON object')
                output=await turn.execute(call.name,args)
            except (ValueError,TypeError):
                output={'status':'error','reason':'Неверные аргументы инструмента'}
            messages.append({'type':'function_call_output','call_id':call.call_id,
                             'output':json.dumps(output,ensure_ascii=False,default=str)})
    return ({'text':'Я проверила данные, но на этом остановлюсь, чтобы ничего не сделать дважды. '
                    'Если остались вопросы, уточни, пожалуйста.', 'emotion':'neutral',
             'memories_to_save':[],'offer_quest':False}, turn)
