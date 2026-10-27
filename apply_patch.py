#!/usr/bin/env python3
"""Safe additive install of Selin Living Rhythm on an existing bot checkout.

Only this script writes project files. It checks anchors, makes backups and is
idempotent. It never copies historical full source files over the user's tree.
"""
from __future__ import annotations

import argparse
import ast
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MARK = 'SELIN_LIVING_RHYTHM_20261027'
SHOP_MARK = 'SELIN_WANDER_SHOP_20261027'
NEW_FILES = (
    'app/services/living_rhythm.py',
    'app/services/living_rhythm_ai.py',
    'app/services/living_rhythm_worker.py',
    'app/handlers/living_rhythm.py',
    'app/services/wandering_shop.py',
    'app/services/wandering_shop_ai.py',
    'app/handlers/wandering_shop.py',
)

AI_CONTEXT = '''
# SELIN_LIVING_RHYTHM_20261027_BEGIN
_living_rhythm_previous_context = build_ai_context

async def build_ai_context(session, profile):
    result = await _living_rhythm_previous_context(session, profile)
    from app.services.living_rhythm import context_summary
    notes = await context_summary(session, profile.id)
    context = dict(result)
    context['memories'] = (str(result.get('memories') or '') + '\\n' + notes).strip()
    return context
# SELIN_LIVING_RHYTHM_20261027_END
'''

NUTRITION_HOOK = '''
# SELIN_LIVING_RHYTHM_20261027_BEGIN
_living_rhythm_record_food_nutrition = record_food_nutrition

async def record_food_nutrition(session, **kwargs):
    row = await _living_rhythm_record_food_nutrition(session, **kwargs)
    from app.services.living_rhythm import observe_entry
    await observe_entry(session, row)
    return row
# SELIN_LIVING_RHYTHM_20261027_END
'''

COACH_HOOK = '''
# SELIN_LIVING_RHYTHM_20261027_BEGIN
# The new worker sends ONE deferred review after a whole meal, not one per item.
# The regular daily/evening review is unchanged.
_living_rhythm_original_intraday = create_intraday_food_feedback

async def create_intraday_food_feedback(user_id, display_name, *, latest_action):
    return None
# SELIN_LIVING_RHYTHM_20261027_END
'''

SCHEDULER_HOOK = '''
# SELIN_LIVING_RHYTHM_20261027_BEGIN
_living_rhythm_original_send_due = send_due

async def send_due(bot):
    from app.services.living_rhythm import suppress_nonessential_autonomy
    await suppress_nonessential_autonomy()
    return await _living_rhythm_original_send_due(bot)
# SELIN_LIVING_RHYTHM_20261027_END
'''

HISTORY_HOOK = '''
# SELIN_LIVING_RHYTHM_20261027_BEGIN
_living_rhythm_original_history = build_nutrition_history

async def build_nutrition_history(session, user_id, *, days, today):
    result = await _living_rhythm_original_history(session, user_id, days=days, today=today)
    from datetime import timedelta
    from app.services.living_rhythm import grouped_history_text
    groups = await grouped_history_text(session, user_id, today - timedelta(days=days), today - timedelta(days=1))
    if groups:
        result = dict(result)
        result['history_text'] = str(result.get('history_text') or '') + (
            '\\n\\nГруппы подтверждённых приёмов пищи (одна группа может включать несколько позиций):\\n' + groups
        )
    return result
# SELIN_LIVING_RHYTHM_20261027_END
'''

RECIPE_HOOK = '''
# SELIN_LIVING_RHYTHM_20261027_BEGIN
_living_rhythm_original_record_recipe_serving = record_recipe_serving

async def record_recipe_serving(session, profile, recipe, day, **kwargs):
    previous = session.info.get('lr_recipe_id')
    previous_portion = session.info.get('lr_portions')
    session.info['lr_recipe_id'] = recipe.id
    session.info['lr_portions'] = kwargs.get('fraction', 1.0)
    try:
        return await _living_rhythm_original_record_recipe_serving(session, profile, recipe, day, **kwargs)
    finally:
        if previous is None:
            session.info.pop('lr_recipe_id', None)
        else:
            session.info['lr_recipe_id'] = previous
        if previous_portion is None:
            session.info.pop('lr_portions', None)
        else:
            session.info['lr_portions'] = previous_portion
# SELIN_LIVING_RHYTHM_20261027_END
'''


SHOP_CONTEXT = r"""
# SELIN_WANDER_SHOP_20261027_BEGIN
_wander_shop_previous_build_ai_context = build_ai_context

async def build_ai_context(session, profile):
    result = await _wander_shop_previous_build_ai_context(session, profile)
    from app.services.wandering_shop import inventory_context
    items = await inventory_context(session, profile.id)
    if not items:
        return result
    result = dict(result)
    result['memories'] = (str(result.get('memories') or '') + '\n' + items).strip()
    return result
# SELIN_WANDER_SHOP_20261027_END
"""


def read(root: Path, name: str) -> str:
    return (root / name).read_text(encoding='utf-8-sig')


def append_once(text: str, code: str) -> str:
    if MARK in text:
        return text
    return text.rstrip() + '\n\n' + code.strip() + '\n'


def patch_main(text: str) -> str:
    if MARK in text:
        return text
    intro = '''# SELIN_LIVING_RHYTHM_20261027_BEGIN
from app.services import living_rhythm as _living_rhythm_model_registry
from app.handlers import living_rhythm as living_rhythm_handlers
from app.services.living_rhythm_worker import rhythm_worker
# SELIN_LIVING_RHYTHM_20261027_END

'''
    text = text.replace('async def main() -> None:', intro + 'async def main() -> None:', 1)
    text = text.replace('    dp.include_router(ai_chat.router)',
                        '    dp.include_router(living_rhythm_handlers.router)\n    dp.include_router(ai_chat.router)', 1)
    text = text.replace('    worker = asyncio.create_task(notification_worker(bot))',
                        '    worker = asyncio.create_task(notification_worker(bot))\n'
                        '    rhythm_task = asyncio.create_task(rhythm_worker(bot))', 1)
    text = text.replace('        worker.cancel()', '        rhythm_task.cancel()\n        worker.cancel()', 1)
    return text


def patch_ai_chat(src: str) -> str:
    if MARK in src:
        return src
    # Explicitly consumed meals no longer escape the dialogue before context
    # interpretation. Other message types still go through original Selin chat.
    src = src.replace('if actor_override is None and (_dialog_idle(await state.get_data()) or _should_switch_to_action(user_text)):',
                      'if actor_override is None and _dialog_idle(await state.get_data()):', 1)
    src = src.replace('if _dialog_idle(data) or _should_switch_to_action(message.text or ""):',
                      'if _dialog_idle(data):', 1)
    anchor = '    actor = actor_override or message.from_user\n'
    insertion = '''    # SELIN_LIVING_RHYTHM_20261027_BEGIN
    from app.handlers.living_rhythm import handle_chat_capture
    if await handle_chat_capture(message, actor, user_text):
        await state.update_data(last_chat_activity_at=now_local().isoformat())
        return
    # SELIN_LIVING_RHYTHM_20261027_END
'''
    src = src.replace(anchor, anchor + insertion, 1)
    # Add initiative hook only if we can identify the right function body.
    anchor2 = '''async def _handle_initiative_message(
    message: Message,
    state: FSMContext,
    notification_id: int,
) -> None:
    user_text = (message.text or "").strip()
'''
    if anchor2 in src:
        src = src.replace(anchor2, anchor2 + '''    from app.handlers.living_rhythm import handle_chat_capture
    if await handle_chat_capture(message, message.from_user, user_text):
        return
''', 1)
    return src


def patch_keyboards(src: str) -> str:
    marker = '[InlineKeyboardButton(text="🧠 Искушение", callback_data="food:temptation")],'
    if 'rhythm:settings' in src or marker not in src:
        return src
    return src.replace(marker, marker + '\n            [InlineKeyboardButton(text="🌿 Живой ритм", callback_data="rhythm:settings")],' + '\n            [InlineKeyboardButton(text="🗓 Приёмы пищи", callback_data="rhythm:diary")],', 1)


def append_mark(source: str, addition: str, mark: str) -> str:
    if mark in source:
        return source
    return source.rstrip() + '\n\n' + addition.strip() + '\n'


def patch_shop_main(source: str) -> str:
    if SHOP_MARK in source:
        return source
    intro = '''# SELIN_WANDER_SHOP_20261027_BEGIN
from app.services import wandering_shop as _wandering_shop_model_registry
from app.handlers import wandering_shop as wandering_shop_handlers
# SELIN_WANDER_SHOP_20261027_END

'''
    source = source.replace('async def main() -> None:', intro + 'async def main() -> None:', 1)
    if '    dp.include_router(ai_chat.router)' in source:
        source = source.replace('    dp.include_router(ai_chat.router)',
            '    dp.include_router(wandering_shop_handlers.router)\n    dp.include_router(ai_chat.router)', 1)
    else:
        raise ValueError('Cannot find chat router insertion point')
    # Closing stale chat state on a shop navigation callback.
    source = source.replace('"shop:", "quest:"', '"shop:", "wander:", "quest:"', 1)
    return source


def patch_shop_keyboards(source: str) -> str:
    if 'callback_data="wander:open"' in source:
        return source
    anchor = '[InlineKeyboardButton(text="🪙 Ателье", callback_data="shop:atelier")],'
    if anchor not in source:
        raise ValueError('Cannot find Atelier button to add shop alongside it')
    return source.replace(anchor,
        '[InlineKeyboardButton(text="🏮 Лавка странствий", callback_data="wander:open")],\n            ' + anchor,
        1)


def build(root: Path) -> dict[str, bytes]:
    updates = {}
    original = read(root, 'app/main.py')
    updates['app/main.py'] = patch_shop_main(patch_main(original)).encode('utf-8')
    original = read(root, 'app/handlers/ai_chat.py')
    updates['app/handlers/ai_chat.py'] = patch_ai_chat(original).encode('utf-8')
    for path, content in [
        ('app/services/ai_features.py', AI_CONTEXT),
        ('app/services/nutrition.py', NUTRITION_HOOK),
        ('app/services/intraday_coach.py', COACH_HOOK),
        ('app/services/scheduler.py', SCHEDULER_HOOK),
    ]:
        
        new_content = append_once(read(root, path), content)
        if path == 'app/services/ai_features.py':
            new_content = append_mark(new_content, SHOP_CONTEXT, SHOP_MARK)
        updates[path] = new_content.encode('utf-8')
    history_file = root / 'app/services/nutrition_history.py'
    if history_file.exists():
        updates['app/services/nutrition_history.py'] = append_once(read(root, 'app/services/nutrition_history.py'), HISTORY_HOOK).encode('utf-8')
    recipe = root / 'app/services/recipe_book.py'
    if recipe.exists():
        updates['app/services/recipe_book.py'] = append_once(read(root, 'app/services/recipe_book.py'), RECIPE_HOOK).encode('utf-8')
    keyboard = root / 'app/keyboards.py'
    if keyboard.is_file():
        updates['app/keyboards.py'] = patch_shop_keyboards(patch_keyboards(read(root, 'app/keyboards.py'))).encode('utf-8')
    for name in NEW_FILES:
        updates[name] = (ROOT / name).read_bytes()
    for name, source in updates.items():
        if name.endswith('.py'):
            ast.parse(source.decode('utf-8'), filename=name)
    return updates


def validate(root: Path) -> list[str]:
    errors = []
    required = {
        'app/main.py': ['async def main() -> None:', 'dp.include_router(ai_chat.router)',
                        'worker = asyncio.create_task(notification_worker(bot))', 'worker.cancel()'],
        'app/services/ai_features.py': ['async def build_ai_context(', 'async def reserve_ai_call('],
        'app/services/ai_engine.py': ['async def _structured_response(', 'async def analyze_food_text(', 'async def generate_selin_reply('],
        'app/services/nutrition.py': ['async def record_food_nutrition(', 'await session.flush()'],
        'app/services/intraday_coach.py': ['async def create_intraday_food_feedback('],
        'app/services/scheduler.py': ['async def send_due('],
        'app/services/challenge_feedback.py': ['async def check_and_notify_challenges('],
        'app/handlers/ai_chat.py': ['async def free_chat_message(',
                                    '    actor = actor_override or message.from_user'],
        'app/db.py': ['async def init_db('],
        'app/models.py': ['class FoodNutritionLog', 'class Notification'],
    }
    for path, anchors in required.items():
        file = root / path
        if not file.is_file():
            errors.append(f'Отсутствует {path}')
            continue
        source = file.read_text(encoding='utf-8-sig')
        if MARK not in source:
            for anchor in anchors:
                if anchor not in source:
                    errors.append(f'{path}: не найдена ожидаемая строка {anchor!r}')
        try:
            ast.parse(source, filename=path)
        except SyntaxError as exc:
            errors.append(f'{path}: изначальная ошибка синтаксиса: {exc}')
    optional_anchors = {
        'app/services/recipe_book.py': 'async def record_recipe_serving(',
        'app/services/nutrition_history.py': 'async def build_nutrition_history(',
    }
    for name, anchor in optional_anchors.items():
        file = root / name
        if file.exists() and MARK not in file.read_text(encoding='utf-8-sig') and anchor not in file.read_text(encoding='utf-8-sig'):
            errors.append(f'{name}: неизвестная версия: нет {anchor!r}')
    for name in NEW_FILES:
        target = root / name
        if target.exists() and target.read_bytes() != (ROOT / name).read_bytes():
            errors.append(f'{name}: уже существует другой файл; перезаписывать опасно')
    return errors


def main() -> int:
    p = argparse.ArgumentParser(description='Living Rhythm + Wandering Shop: additive patch')
    p.add_argument('--project', required=True, type=Path)
    p.add_argument('--check', action='store_true')
    opts = p.parse_args()
    root = opts.project.resolve()
    errors = validate(root)
    if errors:
        print('Не удалось безопасно подготовить патч:\n- ' + '\n- '.join(errors), file=sys.stderr)
        return 1
    try:
        updates = build(root)
    except Exception as exc:
        print(f'Ошибка подготовки: {exc}', file=sys.stderr)
        return 1
    changes = {name: content for name, content in updates.items()
               if not (root / name).exists() or (root / name).read_bytes() != content}
    if opts.check:
        print('Проверка пройдена; файлы НЕ изменены:')
        for name in updates:
            print(('  WRITE  ' if name in changes else '  OK    ') + name)
        return 0
    if not changes:
        print('Патч уже установлен, изменений нет.')
        return 0
    backup_dir = root / '.selin_patch_backups' / ('living_world_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    for name in changes:
        path = root / name
        if path.exists():
            backup = backup_dir / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
    for name, content in changes.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        print('Patched', name)
    print('Старые файлы сохранены в', backup_dir)
    print('После перезапуска init_db создаст новые таблицы ритма и лавки; старые записи остаются.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
