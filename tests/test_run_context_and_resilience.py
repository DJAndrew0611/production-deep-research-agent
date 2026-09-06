import unittest
import asyncio
import time
from core.context import (
    RunContext, BudgetConfig, RunTelemetry, CircuitBreaker,
    BudgetExceededError, StageTimeoutError, CircuitBreakerOpenError
)
from core.resilience import retry_with_backoff, execute_with_timeout

class TestRunContextAndResilience(unittest.TestCase):

    def test_budget_tracker_enforces_search_limits(self):
        ctx = RunContext(topic="AI Agents", budget=BudgetConfig(max_search_calls=2))
        ctx.track_search_call()
        self.assertEqual(ctx.telemetry.search_calls_used, 1)
        ctx.track_search_call()
        self.assertEqual(ctx.telemetry.search_calls_used, 2)
        
        with self.assertRaises(BudgetExceededError) as cm:
            ctx.track_search_call()
        self.assertEqual(cm.exception.resource, "search_calls")
        self.assertEqual(cm.exception.limit, 2)

    def test_budget_tracker_enforces_token_limits(self):
        ctx = RunContext(topic="AI Agents", budget=BudgetConfig(max_tokens=1000))
        ctx.track_tokens(600)
        self.assertEqual(ctx.telemetry.tokens_used, 600)

        with self.assertRaises(BudgetExceededError) as cm:
            ctx.track_tokens(500)
        self.assertEqual(cm.exception.resource, "tokens")
        self.assertEqual(cm.exception.limit, 1000)

    def test_budget_tracker_enforces_cost_limits(self):
        ctx = RunContext(topic="Cost Control", budget=BudgetConfig(max_cost_usd=0.01))
        # 1000 tokens on gpt-4o ($0.005 / 1K)
        ctx.track_tokens(1000, model_name="gpt-4o")
        self.assertAlmostEqual(ctx.telemetry.cost_used_usd, 0.005, places=4)

        # Another 1500 tokens on gpt-4o -> total 0.0125 USD > 0.01 USD limit
        with self.assertRaises(BudgetExceededError) as cm:
            ctx.track_tokens(1500, model_name="gpt-4o")
        self.assertEqual(cm.exception.resource, "cost_usd")
        self.assertAlmostEqual(cm.exception.limit, 0.01, places=4)

    def test_retry_with_backoff_recovers_transient_failures(self):
        calls = 0

        async def flaky_api():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ConnectionError("Temporary network reset")
            return "SUCCESS"

        telemetry = RunTelemetry()
        res = asyncio.run(
            retry_with_backoff(
                flaky_api,
                max_retries=3,
                base_delay=0.01,
                jitter=False,
                retry_exceptions=(ConnectionError,),
                telemetry=telemetry
            )
        )
        self.assertEqual(res, "SUCCESS")
        self.assertEqual(calls, 3)
        self.assertEqual(telemetry.retry_counts, 2)

    def test_retry_with_backoff_does_not_retry_budget_error(self):
        calls = 0

        async def budget_failing_api():
            nonlocal calls
            calls += 1
            raise BudgetExceededError("tokens", 100, 50)

        with self.assertRaises(BudgetExceededError):
            asyncio.run(
                retry_with_backoff(
                    budget_failing_api,
                    max_retries=3,
                    base_delay=0.01
                )
            )
        self.assertEqual(calls, 1)

    def test_circuit_breaker_transitions(self):
        cb = CircuitBreaker(service_name="TestSearch", failure_threshold=2, recovery_timeout=0.1)
        self.assertEqual(cb.state, "CLOSED")

        # Failure 1
        cb.record_failure(Exception("Fail 1"))
        self.assertEqual(cb.state, "CLOSED")
        self.assertTrue(cb.can_execute())

        # Failure 2 -> Opens
        cb.record_failure(Exception("Fail 2"))
        self.assertEqual(cb.state, "OPEN")
        self.assertFalse(cb.can_execute())

        # Try execute while OPEN -> raises CircuitBreakerOpenError
        async def dummy():
            return "OK"

        with self.assertRaises(CircuitBreakerOpenError):
            asyncio.run(cb.execute(dummy))

        # Wait for recovery cooldown
        time.sleep(0.12)
        self.assertTrue(cb.can_execute())
        self.assertEqual(cb.state, "HALF_OPEN")

        # Successful execution transitions back to CLOSED
        res = asyncio.run(cb.execute(dummy))
        self.assertEqual(res, "OK")
        self.assertEqual(cb.state, "CLOSED")
        self.assertEqual(cb.consecutive_failures, 0)

    def test_stage_timeout_interruption(self):
        ctx = RunContext(topic="Timeout Test")

        async def slow_stage():
            await asyncio.sleep(0.2)
            return "done"

        ctx.record_stage_start("stage_test")
        with self.assertRaises(StageTimeoutError) as cm:
            asyncio.run(execute_with_timeout(slow_stage(), timeout_seconds=0.05, stage_name="stage_test", context=ctx))

        self.assertEqual(cm.exception.stage_name, "stage_test")
        self.assertEqual(ctx.telemetry.stage_status.get("stage_test"), "timeout")
        self.assertEqual(len(ctx.telemetry.errors), 1)

if __name__ == "__main__":
    unittest.main()
