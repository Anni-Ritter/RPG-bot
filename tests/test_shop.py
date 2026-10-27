"""Offline SQL transaction smoke: no aiogram or OpenAI needed."""
import asyncio
import importlib
import sys
import types
from pathlib import Path
from datetime import date

from sqlalchemy import JSON, ForeignKey, Integer, String, create_engine, select, func
from sqlalchemy.orm import Mapped, mapped_column, DeclarativeBase, sessionmaker

root = Path(__file__).resolve().parents[1]
app = types.ModuleType('app'); app.__path__ = [str(root / 'app')]
services = types.ModuleType('app.services'); services.__path__ = [str(root / 'app/services')]
config = types.ModuleType('app.config')
config.settings = types.SimpleNamespace(timezone='Europe/Moscow')
class Base(DeclarativeBase): pass
db = types.ModuleType('app.db'); db.Base = Base
models = types.ModuleType('app.models')
class UserProfile(Base):
    __tablename__ = 'user_profiles'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer)
    coins: Mapped[int] = mapped_column(Integer, default=0)
class GameEvent(Base):
    __tablename__ = 'game_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id'))
    event_type: Mapped[str] = mapped_column(String(80))
    coins_delta: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
models.UserProfile=UserProfile; models.GameEvent=GameEvent
sys.modules.update({'app':app,'app.services':services,'app.config':config,'app.db':db,'app.models':models})
shop = importlib.import_module('app.services.wandering_shop')

class AsyncShim:
    def __init__(self, session): self.raw=session
    async def scalar(self, query): return self.raw.scalar(query)
    async def scalars(self, query): return self.raw.scalars(query)
    async def execute(self, query): return self.raw.execute(query)
    async def flush(self): self.raw.flush()
    async def get(self, model, pk): return self.raw.get(model, pk)
    def add(self, instance): self.raw.add(instance)

async def go():
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    factory=sessionmaker(engine, expire_on_commit=False)
    raw=factory(); s=AsyncShim(raw)
    user=UserProfile(telegram_id=1234, coins=300)
    raw.add(user); raw.flush()
    day=date(2026, 10, 27)
    a=shop.fallback_items(42)
    assert len(a)==len(set(x['name'] for x in a))==6
    assert all(x['price'] > 0 and x['price'] <= 240 for x in a)
    assert shop.reroll_cost(None,day)==0
    assert await shop.install_rotation(s,user,a,expected_rotation=0,reroll=False,today=day)==0
    assert len(await shop.current_offers(s,user.id))==6
    first=(await shop.current_offers(s,user.id))[0]
    starting=user.coins
    obtained=await shop.purchase(s,user,first.id)
    assert user.coins==starting-first.price
    assert obtained.offer_id==first.id
    try:
        await shop.purchase(s,user,first.id)
        assert False, 'double purchase was allowed'
    except ValueError as exc:
        assert 'уже куплен' in str(exc)
    assert user.coins==starting-first.price
    await shop.save_reaction(s,user.id,obtained.id,'Тори трогает подарок лапой.')
    assert 'Тори трогает' in await shop.inventory_context(s,user.id)
    used=await shop.use_item(s,user,obtained.id)
    assert used.uses==1
    # First refresh per day is free; second is 5; prices cannot come from AI.
    assert await shop.install_rotation(s,user,shop.fallback_items(43),expected_rotation=1,reroll=True,today=day)==0
    assert shop.reroll_cost(await shop.get_state(s,user.id),day)==5
    assert await shop.install_rotation(s,user,shop.fallback_items(44),expected_rotation=2,reroll=True,today=day)==5
    assert len(await shop.inventory(s,user.id))==1
    assert len(await shop.current_offers(s,user.id))==6
    try:
        await shop.purchase(s,user,first.id)
        assert False, 'stale product was purchased'
    except ValueError as exc:
        assert 'устарел' in str(exc)
    before=user.coins
    try:
        await shop.install_rotation(s,user,shop.fallback_items(45),expected_rotation=2,reroll=True,today=day)
        assert False, 'stale reroll charged user'
    except ValueError as exc:
        assert 'уже обновлена' in str(exc)
    assert user.coins==before
    debit=raw.scalar(select(func.sum(GameEvent.coins_delta)).where(GameEvent.user_id==user.id))
    assert debit == -(first.price+5)
    raw.commit()
    assert raw.scalar(select(func.count(shop.OwnedWorldItem.id)))==1
    print('PASS: persistent 6-item shop, price caps, purchase once, atomic coins, free/paid reroll, old offers invalid, owned items survive, AI context, events')

asyncio.run(go())
