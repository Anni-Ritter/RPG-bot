"""Dependency-free smoke checks for the real Responses API agent's tool contract.

Run: python -m unittest discover -s tests -p 'test_selin_tool_agent_static.py'
"""
import ast
import asyncio
import json
import pathlib
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT = ROOT / 'app/services/selin_agent.py'


def tools_without_imports():
    tree = ast.parse(AGENT.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {'_function','S','I','N'}]
    nodes += [node for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TOOLS' for t in node.targets)]
    ns={}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(AGENT),'exec'),ns)
    return ns['TOOLS']


class ContractTests(unittest.TestCase):
    def test_tool_schema_strict(self):
        tools=tools_without_imports()
        self.assertGreaterEqual(len(tools),15)
        allowed={'type','properties','required','additionalProperties','items','enum','description'}
        self.assertEqual(len({t['name'] for t in tools}),len(tools))
        def check(schema):
            self.assertFalse(set(schema) - allowed, f'Unsupported schema keys: {schema}')
            if schema.get('type')=='object':
                self.assertEqual(schema.get('additionalProperties'),False)
                self.assertEqual(set(schema.get('required',[])),set(schema.get('properties',{})))
                for prop in schema.get('properties',{}).values(): check(prop)
            elif schema.get('type')=='array': check(schema['items'])
        for t in tools:
            self.assertTrue(t['strict'])
            check(t['parameters'])
    def test_agent_wired_without_keyword_intercepts(self):
        source=(ROOT/'app/handlers/ai_chat.py').read_text(encoding='utf-8')
        self.assertIn('agent_reply(',source)
        self.assertNotIn('await handle_chat_capture(',source)
        self.assertNotIn('await intercept_chat_food_edit(',source)
        self.assertNotIn('await handle_conversational_action(',source)
        self.assertIn('send_pending_actions(',source)
        for file,func in [('companion_actions.py','direct_category'),('food_corrections.py','natural_food_edit'),('living_rhythm.py','live_text_message')]:
            tree=ast.parse((ROOT/'app/handlers'/file).read_text(encoding='utf-8'))
            function=next(node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name==func)
            self.assertEqual(function.decorator_list,[])
        main=(ROOT/'app/main.py').read_text(encoding='utf-8')
        self.assertNotIn('dp.message.outer_middleware(ActivityBreakMiddleware())',main)
        self.assertIn('dp.include_router(agent_callbacks_handlers.router)',main)
        self.assertLess(main.index('dp.include_router(agent_callbacks_handlers.router)'),main.index('dp.include_router(ai_chat.router)'))
    def test_pending_is_db_owned_and_revalidated(self):
        source=AGENT.read_text(encoding='utf-8')
        for literal in ['UniqueConstraint(',"status == 'pending'",'with_for_update()',"signature(food) != a['signature']",'offer.rotation',"purchase(session, profile, offer_id)"]:
            if literal=='offer.rotation':
                self.assertIn('current_offers(session, user_id)',source)
            else:
                self.assertIn(literal,source)

    def test_real_tool_call_loop(self):
        tree=ast.parse(AGENT.read_text(encoding='utf-8'))
        node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='agent_reply')
        class StubTurn:
            def __init__(self,*a): self.completed=['Исправлено в базе'];self.pending=[];self.calls=[]
            async def execute(self,name,args): self.calls.append((name,args));return {'status':'success','message':'Изменено'}
        class Api:
            def __init__(self): self.calls=[]
            async def create(self,**kwargs):
                self.calls.append(kwargs)
                if len(self.calls)==1:
                    return types.SimpleNamespace(status='completed',output=[types.SimpleNamespace(type='function_call',call_id='call_123',name='find_food',arguments='{"query":"йогурт"}')],output_text='')
                return types.SimpleNamespace(status='completed',output=[],output_text=json.dumps({'text':'Проверила','emotion':'neutral'}))
        api=Api()
        ns={'AgentTurn':StubTurn,'_selin_system':lambda *args,**kwargs:'system', 'AGENT_RULES':'tools',
            '_client':lambda: types.SimpleNamespace(responses=api), 'settings':types.SimpleNamespace(openai_model='test-model'),
            'TOOLS':tools_without_imports(), 'CHAT_SCHEMA':{'type':'object'}, 'json':json, 'log':types.SimpleNamespace(exception=lambda *args:None),
            'RuntimeError':RuntimeError}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(AGENT),'exec'),ns)
        result, turn=asyncio.run(ns['agent_reply'](user_text='Йогурт неправильно записан',context={},recent_chat='',
                                                       can_offer_quest=False,profile_id=1,message_key='1:2'))
        self.assertEqual(result['text'],'Проверила')
        self.assertEqual(turn.calls,[('find_food',{'query':'йогурт'})])
        self.assertEqual(api.calls[1]['input'][-1]['call_id'],'call_123')
        self.assertEqual(json.loads(api.calls[1]['input'][-1]['output'])['status'],'success')

if __name__=='__main__': unittest.main()
