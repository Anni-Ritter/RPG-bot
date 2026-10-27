import ast,re
from dataclasses import dataclass
from pathlib import Path

src=(Path(__file__).resolve().parents[1] / 'app/services/activity_breaks.py').read_text(encoding='utf8')
tree=ast.parse(src)
keep=[]
for node in tree.body:
 if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'_WORDS','_DURATION','_RESTRICTION','_COLLOQUIAL','_NEAR_WEEK'} for t in node.targets):
  keep.append(node)
 if isinstance(node,ast.FunctionDef) and node.name=='parse_break_proposal': keep.append(node)
ns={'re':re,'BreakProposal':lambda days,reason:{'days':days,'reason':reason}}
future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
exec(compile(ast.fix_missing_locations(ast.Module(body=[future] + keep,type_ignores=[])),'<parser>','exec'),ns)
parse=ns['parse_break_proposal']
samples={
 '\u041a\u0441\u0442\u0430\u0442\u0438, \u043a\u0441\u0442\u0430\u0442\u0438, \u0421\u0435\u043b\u0438\u043d, \u0443 \u043c\u0435\u043d\u044f \u0442\u0430\u0442\u0443 \u0438 \u0431\u043b\u0438\u0436\u0430\u0439\u0448\u0443\u044e \u043d\u0435\u0434\u0435\u043b\u044e \u044f \u0445\u043e\u0434\u0438\u0442\u044c \u043d\u0430 \u0442\u0440\u0435\u043d\u0438\u0440\u043e\u0432\u043a\u0438 \u043d\u0435 \u0431\u0443\u0434\u0443, \u043d\u043e \u043f\u0440\u043e\u0434\u043e\u043b\u0436\u0430\u044e \u0433\u0443\u043b\u044f\u0442\u044c':(7,'tattoo'),
 '\u0414\u0432\u0435 \u043d\u0435\u0434\u0435\u043b\u0438 \u0431\u0435\u0437 \u0441\u043f\u043e\u0440\u0442\u0430':(14,'rest'),
 '\u042f \u0432 \u0431\u043b\u0438\u0436\u0430\u0439\u0448\u0443\u044e \u043d\u0435\u0434\u0435\u043b\u044e \u043d\u0435 \u043f\u043e\u0439\u0434\u0443 \u0432 \u0437\u0430\u043b':(7,'rest'),
 '\u0421\u0435\u0433\u043e\u0434\u043d\u044f \u0438\u0434\u0443 \u043d\u0430 \u0442\u0440\u0435\u043d\u0438\u0440\u043e\u0432\u043a\u0443':None,
 '\u041e\u0431\u043e\u0436\u0430\u044e \u0442\u0430\u0442\u0443, \u043d\u0435\u0434\u0435\u043b\u044e \u0431\u0443\u0434\u0443 \u0433\u0443\u043b\u044f\u0442\u044c':None,
 '\u041d\u0435 \u043c\u043e\u0433\u0443 \u0431\u0435\u0437 \u0442\u0440\u0435\u043d\u0438\u0440\u043e\u0432\u043e\u043a \u0434\u0432\u0435 \u043d\u0435\u0434\u0435\u043b\u0438':None,
 '\u041c\u043e\u0436\u043d\u043e \u043c\u043d\u0435 \u043d\u0435\u0434\u0435\u043b\u044e \u043d\u0435 \u0445\u043e\u0434\u0438\u0442\u044c \u043d\u0430 \u0442\u0440\u0435\u043d\u0438\u0440\u043e\u0432\u043a\u0438?':None,
}
for text,expected in samples.items():
 found=parse(text)
 if found and expected: assert (found['days'],found['reason'])==expected, (text,found,expected)
 else: assert found==expected,(text,found,expected)
print('PARSER OK',len(samples))

root=Path(__file__).resolve().parents[1]
main=(root/'app/main.py').read_text()
assert 'dp.message.outer_middleware(ActivityBreakMiddleware())' in main
assert 'dp.include_router(activity_break_handlers.router)' in main
ai=(root/'app/handlers/ai_chat.py').read_text()
assert 'reply_markup=ai_chat_stop_menu()' not in ai
assert 'reply_markup=ai_initiative_stop_menu' not in ai
assert 'markup = ai_initiative_stop_menu' not in ai
assert '@router.message(FreeSelinChatState.talking, F.text)' in ai
assert 'while_typing(message, generate_selin_reply(' in ai
assert 'from app.services.typing_status import while_typing' in (root/'app/handlers/photo_analysis.py').read_text()
assert '@router.message(Command("selin_status", "what_saved"))' in (root/'app/handlers/activity_breaks.py').read_text()
print('INTEGRATION STATIC OK')
