"""Tests for structured logging and observability."""
import json
import logging
import sys
import os
import asyncio
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.observability import StructuredFormatter, setup_logger, StageTracer, log_pipeline_summary
from core.context import RunContext


class TestStructuredFormatter(unittest.TestCase):
    def test_json_output_format(self):
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test message", args=(), exc_info=None
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        self.assertEqual(parsed["level"], "INFO")
        self.assertEqual(parsed["message"], "test message")

    def test_extra_fields_propagated(self):
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="stage done", args=(), exc_info=None
        )
        record.run_id = "run_abc123"
        record.stage = "stage_1"
        record.duration_ms = 1234.5
        output = formatter.format(record)
        parsed = json.loads(output)
        self.assertEqual(parsed["run_id"], "run_abc123")
        self.assertEqual(parsed["stage"], "stage_1")
        self.assertEqual(parsed["duration_ms"], 1234.5)


class TestStageTracer(unittest.TestCase):
    def test_successful_stage_tracing(self):
        # Use unique logger name to avoid handler duplication
        logger = setup_logger("test_tracer_ok_" + str(id(self)), level=logging.DEBUG)
        ctx = RunContext(topic="Test")
        
        async def _run():
            async with StageTracer(logger, ctx, "test_stage"):
                await asyncio.sleep(0.01)
        
        asyncio.run(_run())
        self.assertEqual(ctx.telemetry.stage_status.get("test_stage"), "completed")
        self.assertIn("test_stage", ctx.telemetry.stage_durations)

    def test_failed_stage_tracing(self):
        logger = setup_logger("test_tracer_fail_" + str(id(self)), level=logging.DEBUG)
        ctx = RunContext(topic="Test")
        
        async def _run():
            async with StageTracer(logger, ctx, "fail_stage"):
                raise ValueError("simulated failure")
        
        with self.assertRaises(ValueError):
            asyncio.run(_run())
        self.assertEqual(ctx.telemetry.stage_status.get("fail_stage"), "error")


class TestPipelineSummary(unittest.TestCase):
    def test_summary_emission(self):
        logger = setup_logger("test_summary_" + str(id(self)), level=logging.DEBUG)
        ctx = RunContext(topic="Summary Test")
        ctx.telemetry.tokens_used = 5000
        ctx.telemetry.search_calls_used = 3
        # Should not raise
        log_pipeline_summary(logger, ctx)


if __name__ == "__main__":
    unittest.main()
