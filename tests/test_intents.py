"""Isolated checks: an AI error cannot convert a wish into a food write."""
import asyncio
import importlib
import sys
import types
from pathlib import Path

folder = Path(__file__).resolve().parents[1] / 'app/services'
app=types.ModuleType('app'); app.__path__=[str(folder.parent)]
services=types.ModuleType('app.services'); services.__path__=[str(folder)]
engine=types.ModuleType('app.services.ai_engine')
engine.ai_enabled=lambda:True
async def fake_text(name):return {'summary':name,'calories_kcal':None,'nutrition_source':'text_estimate','confidence':'low'}
engine.analyze_food_text=fake_text
response={}
async def fake_structured(**kwargs): return response.copy()
engine._structured_response=fake_structured
nutrition=types.ModuleType('app.services.nutrition')
nutrition.normalize_food_result=lambda x:x
sys.modules.update({'app':app,'app.services':services,'app.services.ai_engine':engine,'app.services.nutrition':nutrition})
intent=importlib.import_module('app.services.living_rhythm_ai')

async def run():
    global response
    response={'intent':'craving','meal_kind':'unknown',
              'items':[{'name':'кофе','quantity':1,'category':'drink','consumed':True}],
              'travel_today':False,'skip_training_today':False,'clarifying_question':''}
    result=await intent.interpret('Хочу сладкий кофе')
    assert result['intent']=='craving' and result['items']==[]
    response['intent']='plan'
    result=await intent.interpret('Хочу потом выпить кофе')
    assert result['items']==[]
    response['intent']='consumed'; response['items'][0]['consumed']=False
    result=await intent.interpret('Не выпила кофе')
    assert result['items']==[]
    response['items']=[{'name':'Лепешка','quantity':1,'category':'meal','consumed':True},
                       {'name':'кофе','quantity':1,'category':'drink','consumed':True}]
    response['meal_kind']='breakfast'
    result=await intent.interpret('Съела лепешку и выпила кофе')
    assert len(result['items'])==2 and result['meal_kind']=='breakfast'
    old=await intent.interpret('Вчера съела лепешку и выпила кофе')
    assert old['intent']=='other' and not old['items']
    print('PASS: cravings/plans/negation never produce write suggestions, two confirmed items recognized, yesterday not logged as today')

asyncio.run(run())
