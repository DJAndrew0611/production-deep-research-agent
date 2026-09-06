import asyncio
import random
import time
from typing import Callable, Any, Awaitable, Tuple, Type, Optional
from core.context import (
    ResearchError, BudgetExceededError, StageTimeoutError,
    CircuitBreakerOpenError, RunContext, RunTelemetry
)

NON_RETRYABLE_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    BudgetExceededError,
    StageTimeoutError,
    CircuitBreakerOpenError,
    KeyboardInterrupt,
    SystemExit,
    ValueError
)

async def retry_with_backoff(
    coro_func: Callable[..., Awaitable[Any]],
    *args,
    max_retries: int = 3,
    base_delay: float = 0.5,
    factor: float = 2.0,
    jitter: bool = True,
    retry_exceptions: Tuple[Type[Exception], ...] = (Exception,),
    telemetry: Optional[RunTelemetry] = None,
    **kwargs
) -> Any:
    """
    Execute an asynchronous callable with exponential backoff and jitter.
    Non-retryable exceptions (budget limits, circuit breaker open, timeouts) fail immediately.
    """
    attempt = 0
    while True:
        try:
            return await coro_func(*args, **kwargs)
        except NON_RETRYABLE_EXCEPTIONS:
            raise
        except retry_exceptions as exc:
            attempt += 1
            if telemetry:
                telemetry.record_retry()
            if attempt > max_retries:
                raise exc

            # Calculate backoff delay
            delay = base_delay * (factor ** (attempt - 1))
            if jitter:
                delay = delay * (0.8 + 0.4 * random.random())

            await asyncio.sleep(delay)

async def execute_with_timeout(
    coro: Awaitable[Any],
    timeout_seconds: float,
    stage_name: str = "stage",
    context: Optional[RunContext] = None
) -> Any:
    """
    Execute a coroutine with a strict per-stage timeout.
    If the timeout elapses, StageTimeoutError is raised and telemetry is recorded.
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        if context:
            context.record_stage_end(stage_name, status="timeout")
            context.record_error(stage_name, StageTimeoutError(stage_name, timeout_seconds))
        raise StageTimeoutError(stage_name, timeout_seconds)
