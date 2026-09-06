"""Core framework module for Deep Research Agent."""
from core.context import RunContext, BudgetConfig, RunTelemetry, CircuitBreaker, BudgetExceededError, StageTimeoutError, CircuitBreakerOpenError
from core.resilience import retry_with_backoff, execute_with_timeout

__all__ = [
    "RunContext",
    "BudgetConfig",
    "RunTelemetry",
    "CircuitBreaker",
    "BudgetExceededError",
    "StageTimeoutError",
    "CircuitBreakerOpenError",
    "retry_with_backoff",
    "execute_with_timeout",
    "setup_logger",
    "StageTracer",
    "log_pipeline_summary",
]

from core.observability import setup_logger, StageTracer, log_pipeline_summary
