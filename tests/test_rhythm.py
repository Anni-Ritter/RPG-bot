"""Runs without aiogram/aiosqlite/OpenAI network calls."""
import asyncio
import importlib
import sys
import types
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select
from sqlalchemy.orm import DeclarativeBase, sessionmaker

PKG = Path(__file__).resolve().parents[1]
app = types.ModuleType('app'); app.__path__ = [str(PKG / 'app')]
services = types.ModuleType('app.services'); services.__path__ = [str(PKG / 'app/services')]
settings = types.SimpleNamespace(timezone='Europe/Moscow')
config = types.ModuleType('app.config'); config.settings = settings
class Base(DeclarativeBase): pass
db = types.ModuleType('app.db'); db.Base = Base
sys.modules.update({'app': app, 'app.services': services, 'app.config': config, 'app.db': db})

models = types.ModuleType('app.models')
from sqlalchemy import ForeignKey, Integer, String, Date, DateTime, Float
from sqlalchemy.orm import Mapped, mapped_column

class UserProfile(Base):
    __tablename__ = 'user_profiles'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    coins: Mapped[int] = mapped_column(Integer, default=0)
class FoodNutritionLog(Base):
    __tablename__ = 'food_nutrition_logs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id'))
    logged_on: Mapped[date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(String(300))
    calories_kcal: Mapped[float | None] = mapped_column(Float)
    protein_g: Mapped[float | None] = mapped_column(Float)
    fat_g: Mapped[float | None] = mapped_column(Float)
    carbs_g: Mapped[float | None] = mapped_column(Float)
class DailyStat(Base):
    __tablename__ = 'daily_stats'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id'))
    stat_date: Mapped[date] = mapped_column(Date)
    meals: Mapped[int] = mapped_column(Integer, default=0)
    snacks: Mapped[int] = mapped_column(Integer, default=0)
    drinks: Mapped[int] = mapped_column(Integer, default=0)
    plain_drinks: Mapped[int] = mapped_column(Integer, default=0)
    caloric_drinks: Mapped[int] = mapped_column(Integer, default=0)
    energy_drinks: Mapped[int] = mapped_column(Integer, default=0)
models.DailyStat=DailyStat; models.FoodNutritionLog=FoodNutritionLog; models.UserProfile=UserProfile
class SavedRecipe(Base):
    __tablename__ = 'saved_recipes'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id'))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(30))
    nutrition_source: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[str] = mapped_column(String(20))
    portion_grams: Mapped[float | None] = mapped_column(Float)
    calories_kcal: Mapped[float | None] = mapped_column(Float)
    protein_g: Mapped[float | None] = mapped_column(Float)
    fat_g: Mapped[float | None] = mapped_column(Float)
    carbs_g: Mapped[float | None] = mapped_column(Float)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
class RecipeBatch(Base):
    __tablename__ = 'recipe_batches'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id'))
    recipe_id: Mapped[int] = mapped_column(ForeignKey('saved_recipes.id'))
    remaining_portions: Mapped[float] = mapped_column(Float)
models.SavedRecipe=SavedRecipe; models.RecipeBatch=RecipeBatch
sys.modules['app.models']=models

rhythm=importlib.import_module('app.services.living_rhythm')
engine=create_engine('sqlite:///:memory:')
Base.metadata.create_all(engine)
Session=sessionmaker(engine,expire_on_commit=False)

class AsyncAdapter:
    def __init__(self): self.real=Session()
    @property
    def info(self): return self.real.info
    async def get(self,*args): return self.real.get(*args)
    async def scalar(self,q): return self.real.scalar(q)
    async def scalars(self,q): return self.real.scalars(q)
    async def execute(self,q): return self.real.execute(q)
    async def flush(self): self.real.flush()
    async def commit(self): self.real.commit()
    async def rollback(self): self.real.rollback()
    def add(self,x): self.real.add(x)
    async def close(self): self.real.close()

nutrition=types.ModuleType('app.services.nutrition')
def normalize_food_result(x): return dict(x)
async def log_food(session, *, user_id, logged_on, analysis_id, category, result, fraction=1, portion_label=''):
    row=FoodNutritionLog(user_id=user_id,logged_on=logged_on,category=category,
        description=result['summary'],calories_kcal=(result.get('calories_kcal') or 0)*fraction,
        protein_g=(result.get('protein_g') or 0)*fraction,
        fat_g=(result.get('fat_g') or 0)*fraction,
        carbs_g=(result.get('carbs_g') or 0)*fraction)
    session.add(row);await session.flush()
    await rhythm.observe_entry(session,row)
    return row
nutrition.normalize_food_result=normalize_food_result
nutrition.record_food_nutrition=log_food
sys.modules['app.services.nutrition']=nutrition
rewards=types.ModuleType('app.services.rewards')
async def get_daily(session,userid,day):
    row=await session.scalar(select(DailyStat).where(DailyStat.user_id==userid,DailyStat.stat_date==day))
    if not row:
        row=DailyStat(user_id=userid,stat_date=day,meals=0,snacks=0,drinks=0,
                      plain_drinks=0,caloric_drinks=0,energy_drinks=0)
        session.add(row);await session.flush()
    return row
async def reward(session, profile, *, event_type, xp=0, coins=0, payload=None):
    profile.xp+=xp;profile.coins+=coins
rewards.get_or_create_daily=get_daily;rewards.apply_reward=reward
sys.modules['app.services.rewards']=rewards
hydration=types.ModuleType('app.services.hydration')
async def hydrate(*a,**kw): return False
hydration.record_hydration=hydrate
sys.modules['app.services.hydration']=hydration
recipebook=types.ModuleType('app.services.recipe_book')
def recipe_result(r):
    return {'summary':r.name,'category':r.category,'nutrition_source':r.nutrition_source,
        'confidence':r.confidence,'reference_grams':r.portion_grams,
        'calories_kcal':r.calories_kcal,'protein_g':r.protein_g,'fat_g':r.fat_g,'carbs_g':r.carbs_g}
recipebook.recipe_result=recipe_result
sys.modules['app.services.recipe_book']=recipebook

async def run_tests():
    adapter=AsyncAdapter()
    sess=adapter
    user=UserProfile(telegram_id=555,xp=0,coins=0)
    sess.add(user); await sess.flush()
    today=rhythm.now_local().date()
    item=FoodNutritionLog(user_id=user.id,logged_on=today,category='meal',description='Лепешка',calories_kcal=350)
    sess.add(item); await sess.flush()
    m=await rhythm.observe_entry(sess,item)
    m_again=await rhythm.observe_entry(sess,item)
    assert m.id==m_again.id
    assert len(await rhythm.group_items(sess,m.id))==1
    coffee=FoodNutritionLog(user_id=user.id,logged_on=today,category='drink',description='Кофе',calories_kcal=120)
    sess.add(coffee); await sess.flush()
    m2=await rhythm.observe_entry(sess,coffee)
    assert m.id!=m2.id and m2.kind=='unknown', 'Standalone coffee must not mean breakfast'
    assert await rhythm.merge_group(sess,user.id,m.id,m2.id)
    assert len(await rhythm.group_items(sess,m.id))==2
    assert m2.merged_into_id==m.id
    assert not await rhythm.merge_group(sess,user.id,m.id,m2.id)
    assert rhythm.kind_at(rhythm.now_local(),'drink')=='unknown'
    # Learned breakfast requires 3 real same-weekday observations, not a hard-coded timetable.
    for days in (7,14,21):
        target=today-timedelta(days=days)
        stamp=datetime(target.year,target.month,target.day,7,20,tzinfo=ZoneInfo('Europe/Moscow'))
        await rhythm.create_group(sess,user.id,'breakfast',stamp=stamp)
    pattern=await rhythm.breakfast_pattern(sess,user.id,today)
    assert pattern and pattern['sample_count']==3 and pattern['minute']==440
    # Be careful to avoid treating unrelated beverage as logged meal.
    prefs=await rhythm.get_settings(sess,user.id)
    assert prefs.mode=='normal'
    prefs.mode='calm'; await sess.commit()
    prefs2=await rhythm.get_settings(sess,user.id)
    assert prefs2.mode=='calm'
    # Two saved recipes logged as one breakfast, with one XP reward.
    recipe1=SavedRecipe(user_id=user.id,name='Лепешка',category='meal',nutrition_source='label',
        confidence='high',portion_grams=230,calories_kcal=360,protein_g=20,fat_g=12,carbs_g=40)
    recipe2=SavedRecipe(user_id=user.id,name='Кофе',category='drink',nutrition_source='text_estimate',
        confidence='low',portion_grams=300,calories_kcal=120,protein_g=2,fat_g=2,carbs_g=25)
    sess.add(recipe1);sess.add(recipe2);await sess.flush()
    stock=RecipeBatch(user_id=user.id,recipe_id=recipe1.id,remaining_portions=3)
    sess.add(stock);await sess.flush()
    proposal=await rhythm.create_proposal(sess,user.id,'breakfast',[
        {'name':'Лепешка','category':'meal','quantity':1,'recipe_id':recipe1.id},
        {'name':'Кофе','category':'drink','quantity':1,'recipe_id':recipe2.id},
    ])
    meal,lines=await rhythm.record_confirmed_group(sess,user,proposal)
    await sess.commit()
    stats=sess.real.scalar(select(DailyStat).where(DailyStat.user_id==user.id))
    assert stats.meals==1 and stats.drinks==1, (stats.meals, stats.drinks)
    assert user.xp==20 and user.coins==3
    assert stock.remaining_portions==2
    assert len(await rhythm.group_items(sess,meal.id))==2
    try:
        await rhythm.record_confirmed_group(sess,user,proposal)
        assert False, 'Reconfirm must fail'
    except ValueError:
        pass
    print('PASS: 2-recipe breakfast counts as 1 meal, single XP, recipe stock decremented, no double confirmation; group merging, pattern learning, settings')
    await sess.close()

asyncio.run(run_tests())
