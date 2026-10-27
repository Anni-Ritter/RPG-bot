#!/usr/bin/env python3
"""Install a minimal overlay without overwriting current Selin & Tori handlers.

Examples:
    py apply_patch.py --project "C:\\Users\\User\\selin-tori" --check
    py apply_patch.py --project "C:\\Users\\User\\selin-tori"
"""
from __future__ import annotations

import argparse
import ast
import shutil
import sys
from datetime import datetime
from pathlib import Path

PATCH_MARK = "SELIN_ACTIVITY_BREAK_20261027"
PATCH_ROOT = Path(__file__).resolve().parent

AI_CONTEXT_EXTENSION = '''
# SELIN_ACTIVITY_BREAK_20261027_BEGIN
# Extend the existing context instead of replacing it (other installed patches may
# have changed build_ai_context since the last public version).
_activity_break_old_build_ai_context = build_ai_context

async def build_ai_context(session, profile):
    context = await _activity_break_old_build_ai_context(session, profile)
    from app.services.activity_breaks import get_break, is_in_effect, now_local, reason_label
    row = await get_break(session, profile.id)
    if is_in_effect(row, now_local().date()):
        note = (
            "ВРЕМЕННОЕ ОГРАНИЧЕНИЕ РЕАЛЬНЫХ НАГРУЗОК " + reason_label(row.reason) +
            ". До подтверждённого возврата НЕ предлагать тренировки, спорт, "
            "нагрузочные челленджи или обязательную норму шагов. "
            "Поддерживать обычное питание и восстановление, предлагать только "
            "спокойные альтернативы. Не делать медицинских выводов. "
            "Дата уточнения: " + row.check_on.isoformat() +
            ". Если дата прошла, это НЕ означает разрешение тренироваться."
        )
        context = dict(context)
        context["memories"] = (str(context.get("memories") or "") + "\\n- " + note).strip()
        context["coach_rules"] = (str(context.get("coach_rules") or "") + "\\n" + note).strip()
        context["activity_break_active"] = True
    return context
# SELIN_ACTIVITY_BREAK_20261027_END
'''

CHALLENGES_EXTENSION = '''
# SELIN_ACTIVITY_BREAK_20261027_BEGIN
_activity_break_old_ensure_plan = ensure_plan

async def ensure_plan(session, profile, day=None):
    plan = await _activity_break_old_ensure_plan(session, profile, day)
    from app.services.activity_breaks import adapt_challenge_plan, is_paused, now_local
    if await is_paused(session, profile.id, day or now_local().date()):
        adapt_challenge_plan(plan)
    return plan
# SELIN_ACTIVITY_BREAK_20261027_END
'''

SCHEDULER_EXTENSION = '''
# SELIN_ACTIVITY_BREAK_20261027_BEGIN
_activity_break_old_send_due = send_due

async def send_due(bot):
    # Mark already-scheduled workout messages as handled before normal dispatch.
    # Do not remove/suppress story, food, Tori or suitable challenge messages.
    from app.services.activity_breaks import (
        now_local, send_due_checkins, suppress_due_sport_notifications,
    )
    now = now_local()
    await suppress_due_sport_notifications(now)
    await send_due_checkins(bot, now)
    return await _activity_break_old_send_due(bot)
# SELIN_ACTIVITY_BREAK_20261027_END
'''


def validate_sources(root: Path) -> list[str]:
    required = {
        "app/main.py": ["async def main()", "dp = Dispatcher()", "dp.include_router(common.router)"],
        "app/services/ai_features.py": ["async def build_ai_context("],
        "app/services/challenges.py": ["async def ensure_plan("],
        "app/services/scheduler.py": ["async def send_due("],
        "app/db.py": ["init_db"],
        "app/models.py": ["class UserProfile"],
    }
    errors = []
    for name, anchors in required.items():
        path = root / name
        if not path.is_file():
            errors.append(f"Missing {name}")
            continue
        src = path.read_text(encoding="utf-8-sig")
        for anchor in anchors:
            if anchor not in src:
                errors.append(f"{name}: missing expected anchor {anchor!r}")
        if name.endswith(".py"):
            try:
                ast.parse(src, filename=name)
            except SyntaxError as exc:
                errors.append(f"Existing syntax error in {name}: {exc}")
    for rel in ("app/services/activity_breaks.py", "app/handlers/activity_breaks.py"):
        existing = root / rel
        if existing.exists() and existing.read_bytes() != (PATCH_ROOT / rel).read_bytes():
            errors.append(f"{rel} already exists with different content; will not overwrite")
    return errors


def update_main(src: str) -> str:
    if PATCH_MARK in src:
        return src
    # Imports at module execution time register the new SQLAlchemy model before init_db.
    marker = "async def main()"
    addition = (
        "# SELIN_ACTIVITY_BREAK_20261027_BEGIN\\n"
        "from app.handlers import activity_breaks as activity_break_handlers\\n"
        "from app.handlers.activity_breaks import ActivityBreakMiddleware\\n"
        "from app.services import activity_breaks as _activity_break_model_registry\\n"
        "# SELIN_ACTIVITY_BREAK_20261027_END\\n\\n"
    ).replace("\\n", "\n")
    src = src.replace(marker, addition + marker, 1)
    src = src.replace("    dp = Dispatcher()", "    dp = Dispatcher()\n    dp.message.middleware(ActivityBreakMiddleware())", 1)
    src = src.replace("    dp.include_router(common.router)",
                      "    dp.include_router(activity_break_handlers.router)\n    dp.include_router(common.router)", 1)
    return src


def patched(root: Path) -> dict[str, bytes]:
    result = {}
    rel = "app/main.py"
    result[rel] = update_main((root / rel).read_text(encoding="utf-8-sig")).encode("utf-8")
    for rel, addition in {
        "app/services/ai_features.py": AI_CONTEXT_EXTENSION,
        "app/services/challenges.py": CHALLENGES_EXTENSION,
        "app/services/scheduler.py": SCHEDULER_EXTENSION,
    }.items():
        orig = (root / rel).read_text(encoding="utf-8-sig")
        result[rel] = (orig if PATCH_MARK in orig else orig.rstrip("\n") + "\n\n" + addition).encode("utf-8")
    for rel in ("app/services/activity_breaks.py", "app/handlers/activity_breaks.py"):
        result[rel] = (PATCH_ROOT / rel).read_bytes()
    for name, data in result.items():
        ast.parse(data.decode("utf-8"), filename=name)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Selin & Tori activity break patch")
    parser.add_argument("--project", required=True, type=Path, help="Current bot source folder")
    parser.add_argument("--check", action="store_true", help="Validate without modifying files")
    args = parser.parse_args()
    project = args.project.resolve()
    errors = validate_sources(project)
    if errors:
        print("Patch cannot be safely applied:\n- " + "\n- ".join(errors), file=sys.stderr)
        return 1
    try:
        changes = patched(project)
    except Exception as exc:
        print(f"Patch preparation failed: {exc}", file=sys.stderr)
        return 1
    changed = {rel: content for rel, content in changes.items()
               if not (project / rel).is_file() or (project / rel).read_bytes() != content}
    if args.check:
        print("Validated (no files modified):")
        for rel in changes:
            print("  " + ("UPDATE " if rel in changed else "OK     ") + rel)
        return 0
    if not changed:
        print("Activity break patch is already installed; nothing changed.")
        return 0
    backup_dir = project / ".selin_patch_backups" / ("activity_break_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    for rel in changed:
        target = project / rel
        if target.is_file():
            backup = backup_dir / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
    for rel, content in changed.items():
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        print("Patched", rel)
    print("Only the listed files were changed. Backups (if any):", backup_dir)
    print("Restart/deploy the bot; its new table is created by init_db().")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
