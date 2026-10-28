from __future__ import annotations
import ast
import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
source = (PROJECT_ROOT / 'app/services/companion_actions.py').read_text(encoding='utf-8')
tree=ast.parse(source)
keep=[]
for node in tree.body:
    if isinstance(node,ast.Assign) and all(isinstance(t,ast.Name) for t in node.targets):
        if any(t.id in {'_EDIT','_CATEGORY','_FOOD_WORDS','_STOP'} for t in node.targets):
            keep.append(node)
    if isinstance(node,ast.FunctionDef) and node.name in {'category_request','requested_category','query_tokens','matching_records','guard_unverified_claim','requested_calorie_fix'}:
        keep.append(node)
ns={'re':re}
exec(compile(ast.fix_missing_locations(ast.Module(body=keep, type_ignores=[])), '<pure_actions>', 'exec'), ns)
cat=ns['requested_category']; request=ns['category_request']; match=ns['matching_records']

@dataclass
class Row:
    description: str

rows=[Row('Греческий йогурт с какао и воздушным рисом'),Row('Лепёшка с индейкой и салатом'),Row('Кофе 3-в-1 лимончелло')]
cases=[
  ('Поменяй категорию йогурта на полноценную еду',True,'meal','Греческий'),
  ('Поменяй категорию йогурта',True,None,'Греческий'),
  ('А отмечено всё равно как перекус',True,None,None),
  ('Перенеси кофе в напитки',True,'drink','Кофе'), # recognized separately via legacy editor
  ('Я хочу сладкий кофе, но пока не буду',False,None,None),
  ('Съела лепешку утром',False,None,'Лепёшка'),
  ('Отметь категорию йогурта как вкусняшку',True,'treat','Греческий'),
  ('Измени категорию кофе на напиток',True,'drink','Кофе'),
]
for phrase, expected, c, substr in cases:
    assert request(phrase)==expected,(phrase,'request',request(phrase),expected)
    assert cat(phrase)==c,(phrase,'category',cat(phrase),c)
    if substr:
       found=match(rows,phrase)
       assert any(substr in r.description for r in found),(phrase,found)

assert 'не могу подтвердить' in ns['guard_unverified_claim']('Поменяй категорию йогурта','Поменяла, теперь еда.')
assert ns['guard_unverified_claim']('Поговорим про работу','Привет!')=='Привет!'
assert ns['requested_calorie_fix']('Исправь калории йогурта на 220 ккал') == 220
assert ns['requested_calorie_fix']('Йогурт примерно 220 ккал') is None
assert ns['requested_calorie_fix']('Хочу йогурт 220 ккал') is None

root=PROJECT_ROOT
main=(root/'app/main.py').read_text()
assert main.index('dp.include_router(companion_actions_handlers.router)')<main.index('dp.include_router(food_corrections_handlers.router)')
assert main.index('dp.include_router(chat_fallback_handlers.router)')>main.index('dp.include_router(photo_analysis.router)')
chat=(root/'app/handlers/ai_chat.py').read_text()
assert chat.count('handle_conversational_action(message,')==2
rhythm=(root/'app/handlers/living_rhythm.py').read_text()
assert 'CompanionActionReceipt(' in rhythm and 'record_confirmed_group(session, profile, proposal)' in rhythm
assert 'check_and_notify_challenges' in rhythm
print('PASS: 8 intent/candidate cases, hallucination guard, routing order and auto-recipe wiring')
