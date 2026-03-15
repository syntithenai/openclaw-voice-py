"""Regression tests for ToolRouter compatibility with LLM tool-call argument names."""

from __future__ import annotations

import tempfile
import unittest

from orchestrator.tools.alarm import AlarmManager
from orchestrator.tools.router import ToolRouter
from orchestrator.tools.state import StateManager
from orchestrator.tools.timer import TimerManager


class ToolRouterLlmArgsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.state = StateManager(workspace_root=self._tmpdir.name, debounce_ms=1)
        self.timers = TimerManager(state_manager=self.state)
        self.alarms = AlarmManager(state_manager=self.state)
        self.router = ToolRouter(timer_manager=self.timers, alarm_manager=self.alarms)

    async def asyncTearDown(self) -> None:
        self._tmpdir.cleanup()

    async def test_set_alarm_accepts_time_str_and_name(self) -> None:
        result = await self.router.execute_tool(
            "set_alarm",
            {"time_str": "in 1 minute", "name": "wake up"},
        )

        self.assertTrue(result.get("success"), result)
        payload = result.get("result", {})
        self.assertIn("alarm_id", payload)
        self.assertEqual(payload.get("label"), "wake up")
        self.assertIn("set for", payload.get("response", ""))

    async def test_set_alarm_accepts_numeric_time_str_minutes(self) -> None:
        result = await self.router.execute_tool(
            "set_alarm",
            {"time_str": "5", "name": "meeting"},
        )

        self.assertTrue(result.get("success"), result)
        payload = result.get("result", {})
        self.assertIn("alarm_id", payload)
        self.assertEqual(payload.get("label"), "meeting")

    async def test_set_alarm_accepts_numeric_time_str_number(self) -> None:
        result = await self.router.execute_tool(
            "set_alarm",
            {"time_str": 5, "name": "meeting"},
        )

        self.assertTrue(result.get("success"), result)
        payload = result.get("result", {})
        self.assertIn("alarm_id", payload)
        self.assertEqual(payload.get("label"), "meeting")

    async def test_cancel_alarm_accepts_name(self) -> None:
        created = await self.router.execute_tool(
            "set_alarm",
            {"time_str": "in 2 minutes", "name": "meeting"},
        )
        self.assertTrue(created.get("success"), created)

        cancelled = await self.router.execute_tool("cancel_alarm", {"name": "meeting"})
        self.assertTrue(cancelled.get("success"), cancelled)
        payload = cancelled.get("result", {})
        self.assertEqual(payload.get("cancelled_count"), 1)

    async def test_set_timer_accepts_name_alias(self) -> None:
        result = await self.router.execute_tool(
            "set_timer",
            {"duration_seconds": 30, "name": "fredofrog"},
        )

        self.assertTrue(result.get("success"), result)
        payload = result.get("result", {})
        self.assertEqual(payload.get("label"), "fredofrog")
        self.assertIn("fredofrog timer", payload.get("response", "").lower())

    async def test_stop_alarm_accepts_name(self) -> None:
        created = await self.router.execute_tool(
            "set_alarm",
            {"time_str": "in 1 minute", "name": "teakettle"},
        )
        self.assertTrue(created.get("success"), created)
        alarm_id = created.get("result", {}).get("alarm_id")
        self.assertTrue(alarm_id)

        # Simulate alarm entering ringing state, then stop by LLM-style `name`.
        await self.alarms.trigger_alarm(alarm_id)
        stopped = await self.router.execute_tool("stop_alarm", {"name": "teakettle"})

        self.assertTrue(stopped.get("success"), stopped)
        payload = stopped.get("result", {})
        self.assertEqual(payload.get("stopped_count"), 1)

    async def test_fast_path_stop_the_alarm_does_not_use_the_as_label(self) -> None:
        """'Stop the alarm.' must not extract 'the' as a label — regression for fast-path bug."""
        created = await self.router.execute_tool(
            "set_alarm",
            {"time_str": "in 1 minute", "name": ""},
        )
        self.assertTrue(created.get("success"), created)
        alarm_id = created.get("result", {}).get("alarm_id")

        await self.alarms.trigger_alarm(alarm_id)

        # fast-path should recognise this as a generic stop (no label)
        result = await self.router.try_deterministic_parse("Stop the alarm.")
        self.assertIsNotNone(result, "fast-path should have matched 'Stop the alarm.'")
        self.assertEqual(result.get("data", {}).get("stopped_count"), 1)

    async def test_fast_path_stop_labeled_alarm(self) -> None:
        """'Stop the morning alarm' should extract label='morning' from the fast-path."""
        created = await self.router.execute_tool(
            "set_alarm",
            {"time_str": "in 1 minute", "name": "morning"},
        )
        self.assertTrue(created.get("success"), created)
        alarm_id = created.get("result", {}).get("alarm_id")

        await self.alarms.trigger_alarm(alarm_id)

        result = await self.router.try_deterministic_parse("Stop the morning alarm.")
        self.assertIsNotNone(result)
        self.assertEqual(result.get("data", {}).get("stopped_count"), 1)

    async def test_fast_path_cancel_the_timer_is_not_label_the(self) -> None:
        """'Cancel the timer' should cancel all timers, not look for label='the'."""
        created = await self.router.execute_tool(
            "set_timer",
            {"duration_seconds": 30, "name": ""},
        )
        self.assertTrue(created.get("success"), created)

        result = await self.router.try_deterministic_parse("Cancel the timer.")
        self.assertIsNotNone(result, "fast-path should have matched 'Cancel the timer.'")
        self.assertEqual(result.get("data", {}).get("cancelled_count"), 1)


if __name__ == "__main__":
    unittest.main()
