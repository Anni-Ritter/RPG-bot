"""Pure-function and wiring smoke tests; no installed aiogram/SQLAlchemy required."""
import ast
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def subset(path, functions, vars=()):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    selected = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in functions:
            selected.append(n)
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in vars for t in n.targets):
            selected.append(n)
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    ns = {'re': re, 'hashlib': hashlib, 'json': json, 'timedelta': timedelta,
          'now_local': lambda: datetime(2026, 10, 27, 15, tzinfo=ZoneInfo('Europe/Moscow')),
          'FIELDS': ('calories_kcal', 'protein_g', 'fat_g', 'carbs_g')}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future] + selected, type_ignores=[])), str(path), 'exec'), ns)
    return ns


handler = subset(ROOT/'app/handlers/food_corrections.py',
                 {'is_edit_intent','_words','_find_matches','_parse_value'},
                 {'EDIT_WORDS','TARGET_STOP'})
service = subset(ROOT/'app/services/food_corrections.py', {'signature'})
breaks = subset(ROOT/'app/services/activity_breaks.py', {'parse_break_proposal'},
                {'_WORDS','_DURATION','_RESTRICTION','_COLLOQUIAL','_NEAR_WEEK'})
breaks['BreakProposal'] = lambda days, reason: (days, reason)

assert handler['is_edit_intent']('Селин, удали последний кофе')
assert handler['is_edit_intent']('Исправь лепёшку: в ней было 80 г сыра вместо 30')
assert handler['is_edit_intent']('В лепёшке было 80 г сыра вместо 30')
assert handler['is_edit_intent']('Селин, я неправильно записала кофе')
assert not handler['is_edit_intent']('Привет, хочу кофе, но не пила')
assert not handler['is_edit_intent']('Удали мою тревогу, я устала')

@dataclass
class Row:
    id: int = 5
    logged_on: date = date(2026, 10, 27)
    category: str = 'drink'
    description: str = 'Кофе с сиропом'
    portion_label: str = '250 г'
    portion_grams: float = 250
    calories_kcal: float = 200
    protein_g: float = 3
    fat_g: float = 5
    carbs_g: float = 38
    nutrition_source: str = 'label'
    confidence: str = 'high'

row = Row()
assert handler['_find_matches']([row], 'удали последний кофе')[0] is row
assert service['signature'](row) != service['signature'](Row(calories_kcal=190))
assert handler['_parse_value']('grams','125',row)['calories_kcal'] == 100
assert handler['_parse_value']('kcal', '143', row)['calories_kcal'] == 143
assert handler['_parse_value']('macros','450 20 12 40',row)['protein_g'] == 20
assert handler['_parse_value']('category','напиток',row)['category'] == 'drink'
for bad in ('-10','10000','фу'):
    try:
        handler['_parse_value']('kcal',bad,row)
        assert False, f'Should reject {bad}'
    except ValueError:
        pass

cases = {
    'Кстати Селин у меня тату и в ближайшую неделю я ходить на тренировки не буду': (7,'tattoo'),
    'Две недели без спорта': (14,'rest'),
    'Сегодня иду на тренировку': None,
    'Можно мне неделю не ходить на тренировки?': None,
}
for src, expected in cases.items():
    assert breaks['parse_break_proposal'](src) == expected, (src, expected)

main = (ROOT/'app/main.py').read_text()
chat = (ROOT/'app/handlers/ai_chat.py').read_text()
keyboard = (ROOT/'app/keyboards.py').read_text()
corrections = (ROOT/'app/services/food_corrections.py').read_text()
assert 'dp.include_router(food_corrections_handlers.router)' in main
assert chat.count('intercept_chat_food_edit(message') >= 2
assert 'fed:list' in keyboard
assert 'with_for_update()' in corrections
assert 'manual_food_undone' in corrections
assert 'apply_reward(' not in corrections
print('PASS: 22 food/break safety and routing checks')
