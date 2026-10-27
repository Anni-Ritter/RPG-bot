"""Offline regression tests for the 2026-10-27 challenge/image AI hotfix.

Run from project root: py -m unittest discover -s tests -p test_ai_logs_fix.py
No bot token, Telegram, SQLite or OpenAI connection is needed.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ENGINE_PATH = Path(__file__).resolve().parents[1] / "app" / "services" / "ai_engine.py"


def load_engine():
    fake_app = types.ModuleType("app")
    fake_app.__path__ = [str(ENGINE_PATH.parents[1])]
    fake_config = types.ModuleType("app.config")
    fake_config.settings = types.SimpleNamespace(openai_model="mock-model", openai_api_key="test-key")
    fake_openai = types.ModuleType("openai")
    fake_openai.AsyncOpenAI = lambda **kwargs: None
    spec = importlib.util.spec_from_file_location("_selin_ai_engine_hotfix_test", ENGINE_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"app": fake_app, "app.config": fake_config, "openai": fake_openai}):
        spec.loader.exec_module(module)
    return module


class FakeResult:
    def __init__(self, text="", status="completed", details=None):
        self.output_text = text
        self.status = status
        self.incomplete_details = details


class FakeResponses:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.results.pop(0)


class AILogFixTests(unittest.TestCase):
    def setUp(self):
        self.engine = load_engine()

    def test_recent_history_is_accepted_and_used(self):
        captured = {}
        async def fake_structured(**kwargs):
            captured.update(kwargs)
            return {"options": []}
        self.engine._structured_response = fake_structured
        context = dict(steps=0, water=0, meals=0, snacks=0, treats_logged=0,
                       energy_drinks=0, level=1, story_day=1)
        result = asyncio.run(self.engine.generate_daily_challenges(
            context=context, coach_rules="", recent_history="2026-10-26: Марш-бросок"
        ))
        self.assertEqual(result, {"options": []})
        self.assertIn("2026-10-26: Марш-бросок", captured["user_content"])

    def test_challenge_schema_includes_existing_codes(self):
        expected = {"steps", "water", "meals", "workout", "food_logs",
                    "treat_limit", "energy_limit", "protein_meals", "tori_bond",
                    "tori_interactions", "willpower_actions", "quests"}
        actual = set(self.engine.CHALLENGE_SCHEMA["properties"]["options"]["items"]["properties"]["code"]["enum"])
        self.assertEqual(actual, expected)

    def test_food_and_activity_images_use_larger_output_budget(self):
        captured = []
        async def fake_structured(**kwargs):
            captured.append(kwargs)
            return {"summary": "ok"}
        self.engine._structured_response = fake_structured
        asyncio.run(self.engine.analyze_food_images([(b"image", "image/jpeg")]))
        asyncio.run(self.engine.analyze_activity_image(b"image", "image/png"))
        self.assertEqual(captured[0]["max_tokens"], 1400)
        self.assertEqual(captured[1]["max_tokens"], 850)
        self.assertEqual(captured[0]["schema_name"], "food_image_analysis_v9")
        self.assertEqual(captured[1]["schema_name"], "activity_image_analysis")

    def test_retry_with_diagnostic_when_output_is_incomplete(self):
        responses = FakeResponses(
            FakeResult(status="incomplete", details={"reason": "max_output_tokens"}),
            FakeResult(text=json.dumps({"summary": "ok"})),
        )
        self.engine._client = lambda: types.SimpleNamespace(responses=responses)
        result = asyncio.run(self.engine._structured_response(
            system="test", user_content="test", schema_name="food_image_analysis_v9",
            schema=self.engine.FOOD_SCHEMA, max_tokens=1400
        ))
        self.assertEqual(result["summary"], "ok")
        self.assertEqual([c["max_output_tokens"] for c in responses.calls], [1400, 2800])

    def test_final_error_explains_last_failure_without_data(self):
        responses = FakeResponses(
            FakeResult(status="incomplete", details={"reason": "max_output_tokens"}),
            FakeResult(status="incomplete", details={"reason": "max_output_tokens"}),
        )
        self.engine._client = lambda: types.SimpleNamespace(responses=responses)
        with self.assertRaisesRegex(RuntimeError, "incomplete_details") as cm:
            asyncio.run(self.engine._structured_response(
                system="test", user_content="PRIVATE_DIARY", schema_name="food_image_analysis_v9",
                schema=self.engine.FOOD_SCHEMA, max_tokens=1400
            ))
        self.assertNotIn("PRIVATE_DIARY", str(cm.exception))
        self.assertIn("food_image_analysis_v9", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
