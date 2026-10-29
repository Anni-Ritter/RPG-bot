"""Offline integration checks for the new world-event persistence and guards.

Runs without aiogram/aiosqlite/OpenAI keys; real SQLAlchemy SQLite persistence.
"""
import asyncio
import runpy
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy import Column, Integer, String, ForeignKey, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session

SOURCE = Path(__file__).resolve().parents[1] / 'app/services/world_autonomy.py'


class Base(DeclarativeBase):
    pass


class Profile(Base):
    __tablename__ = 'user_profiles'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)


class Notice(Base):
    __tablename__ = 'notifications'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)


class Item(Base):
    __tablename__ = 'selin_wander_owned_items'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id'))
    name: Mapped[str] = mapped_column(String(110))
    description: Mapped[str] = mapped_column(String(250))
    recipient: Mapped[str] = mapped_column(String(24))
    use_hint: Mapped[str] = mapped_column(String(240))
    uses: Mapped[int] = mapped_column(Integer, default=0)
    last_reaction: Mapped[str] = mapped_column(String(1000), default='')


async def use_item(session, profile, item_id):
    item = await session.scalar(select(Item).where(Item.id == item_id, Item.user_id == profile.id))
    if item is None:
        raise ValueError('not owned')
    item.uses += 1
    return item


async def save_reaction(session, user_id, item_id, text, event_type):
    item = await session.scalar(select(Item).where(Item.id == item_id, Item.user_id == user_id))
    item.last_reaction = text


class AsyncSyncSession:
    def __init__(self, real): self.real = real
    async def scalar(self, stmt): return self.real.scalar(stmt)
    def add(self, obj): self.real.add(obj)
    async def flush(self): self.real.flush()


def install_module():
    def mod(name, **kw):
        m = types.ModuleType(name)
        m.__dict__.update(kw)
        sys.modules[name] = m
    for name in ['app','app.services']:
        mod(name)
    mod('app.db', Base=Base)
    mod('app.services.ai_features', now_local=lambda: datetime.now(timezone.utc))
    mod('app.services.wandering_shop', OwnedWorldItem=Item, inventory=AsyncMock(),
        use_item=use_item, save_reaction=save_reaction)
    mod('app.services.ai_engine', _structured_response=AsyncMock(),
        SELIN_EMOTIONS=['neutral','curious'], TORI_EMOTIONS=['curious','happy'])
    return runpy.run_path(str(SOURCE))


class WorldTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = install_module()
        cls.engine = create_engine('sqlite://')
        Base.metadata.create_all(cls.engine)

    def setUp(self):
        with Session(self.engine) as db:
            for table in reversed(Base.metadata.sorted_tables):
                db.execute(table.delete())
            db.add_all([Profile(id=1), Profile(id=2), Notice(id=3), Notice(id=4),
                        Item(id=10,user_id=1,name='Ленточки',description='Ленты',recipient='tori',
                             use_hint='Тори играет',uses=0,last_reaction=''),
                        Item(id=11,user_id=2,name='Чужая вещь',description='-',recipient='selin',
                             use_hint='-',uses=0,last_reaction='')])
            db.commit()

    async def test_scene_records_actual_use_and_guards_duplicate(self):
        with Session(self.engine) as db:
            session = AsyncSyncSession(db)
            notice = db.get(Notice,3)
            profile = db.get(Profile,1)
            item = db.get(Item,10)
            scene={'character':'tori','text':'Тори вытащил купленные ленточки и разложил их на подстилке.',
                   'emotion':'happy','uses_item':True}
            row=await self.m['apply_world_scene'](session,profile,notice,scene,item)
            again=await self.m['apply_world_scene'](session,profile,notice,scene,item)
            self.assertEqual(row.id,again.id)
            self.assertEqual(item.uses,1)
            self.assertIn('ленточки',item.last_reaction)
            db.commit()
        with Session(self.engine) as db:
            self.assertEqual(len(db.scalars(select(self.m['WorldScene'])).all()),1)

    async def test_cannot_use_another_users_item(self):
        with Session(self.engine) as db:
            sess=AsyncSyncSession(db)
            with self.assertRaises(ValueError):
                await self.m['apply_world_scene'](sess,db.get(Profile,1),db.get(Notice,4),
                    {'character':'selin','text':'Чужой предмет внезапно оказался в кадре.',
                     'emotion':'neutral','uses_item':True},db.get(Item,11))

    async def test_item_selection_avoids_recent_use(self):
        from types import SimpleNamespace
        item=SimpleNamespace(id=10,uses=0)
        other=SimpleNamespace(id=12,uses=1)
        previous=[SimpleNamespace(item_id=10)]
        class Rng:
            def random(self): return 0.9
            def choice(self,items): return items[0]
        selected=self.m['select_scene_item']([item,other],previous,rng=Rng())
        self.assertEqual(selected.id,12)
        self.assertEqual(self.m['select_character'](1,item,rng=Rng()),'selin')

    async def test_model_cannot_select_unowned_id(self):
        from app.services.ai_engine import _structured_response
        _structured_response.return_value={'text':'Тори раскатал подаренные ленточки по полу и присел на их кончик.',
                                           'emotion':'happy','uses_item':True,'item_id':9999}
        ctx={'story_day':3,'memories':'любимые ленточки','objective':'идти вперёд','tori_bond':42}
        result=await self.m['generate_world_scene'](context=ctx,period='day',actor='tori',
                                                    item=None,history=[])
        self.assertIs(result['uses_item'],False)
        self.assertNotIn('item_id',result)


if __name__ == '__main__':
    unittest.main()
