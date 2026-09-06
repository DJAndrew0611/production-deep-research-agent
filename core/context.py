import time
import uuid
from typing import Dict, Any, List, Optional, Callable, Awaitable
from dataclasses import dataclass, field

class ResearchError(Exception):
    """Base exception for research execution errors."""
    pass

class BudgetExceededError(ResearchError):
    """Raised when token, search call, or cost budget limit is breached."""
    def __init__(self, resource: str, current: Any, limit: Any):
        if isinstance(current, float) or isinstance(limit, float):
            msg = f"Budget exceeded for {resource}: current={float(current):.4f}, limit={float(limit):.4f}"
        else:
            msg = f"Budget exceeded for {resource}: current={current}, limit={limit}"
        super().__init__(msg)
        self.resource = resource
        self.current = current
        self.limit = limit

class StageTimeoutError(ResearchError):
    """Raised when a specific pipeline stage times out."""
    def __init__(self, stage_name: str, timeout_seconds: float):
        super().__init__(f"Stage '{stage_name}' timed out after {timeout_seconds:.1f}s")
        self.stage_name = stage_name
        self.timeout_seconds = timeout_seconds

class CircuitBreakerOpenError(ResearchError):
    """Raised when calling an external service while CircuitBreaker is in OPEN state."""
    def __init__(self, service_name: str, recovery_remaining: float):
        super().__init__(f"Circuit breaker is OPEN for {service_name}. Cooldown remaining: {recovery_remaining:.1f}s")
        self.service_name = service_name
        self.recovery_remaining = recovery_remaining

@dataclass
class BudgetConfig:
    """Configurable resource budget for a research run."""
    max_tokens: int = 60000
    max_search_calls: int = 15
    max_cost_usd: float = 2.0

@dataclass
class RunTelemetry:
    """Operational telemetry collected during a research pipeline execution."""
    tokens_used: int = 0
    search_calls_used: int = 0
    cost_used_usd: float = 0.0
    stage_durations: Dict[str, float] = field(default_factory=dict)
    stage_status: Dict[str, str] = field(default_factory=dict)
    retry_counts: int = 0
    errors: List[Dict[str, Any]] = field(default_factory=list)
    _stage_start_times: Dict[str, float] = field(default_factory=dict)

    def record_stage_start(self, stage_name: str):
        self._stage_start_times[stage_name] = time.time()
        self.stage_status[stage_name] = "running"

    def record_stage_end(self, stage_name: str, status: str = "completed"):
        start_t = self._stage_start_times.get(stage_name)
        if start_t:
            duration = time.time() - start_t
            self.stage_durations[stage_name] = round(duration, 3)
        self.stage_status[stage_name] = status

    def record_retry(self):
        self.retry_counts += 1

    def record_error(self, stage_name: str, error: Exception):
        self.errors.append({
            "stage": stage_name,
            "error_type": type(error).__name__,
            "message": str(error),
            "timestamp": time.time()
        })

class CircuitBreaker:
    """
    Circuit breaker state machine (CLOSED -> OPEN -> HALF_OPEN).
    Protects downstream LLM or search services from cascading failure storms.
    """
    def __init__(self, service_name: str = "ExternalService", failure_threshold: int = 3, recovery_timeout: float = 30.0):
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = "CLOSED"
        self.consecutive_failures = 0
        self.last_failure_time = 0.0

    def can_execute(self) -> bool:
        if self.state == "CLOSED":
            return True
        now = time.time()
        elapsed = now - self.last_failure_time
        if self.state == "OPEN":
            if elapsed >= self.recovery_timeout:
                self.state = "HALF_OPEN"
                return True
            return False
        if self.state == "HALF_OPEN":
            return True
        return False

    def record_success(self):
        self.consecutive_failures = 0
        self.state = "CLOSED"

    def record_failure(self, error: Optional[Exception] = None):
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        if self.consecutive_failures >= self.failure_threshold:
            self.state = "OPEN"

    async def execute(self, coro_func: Callable[..., Awaitable[Any]], *args, **kwargs) -> Any:
        if not self.can_execute():
            remaining = max(0.0, self.recovery_timeout - (time.time() - self.last_failure_time))
            raise CircuitBreakerOpenError(self.service_name, remaining)
        try:
            res = await coro_func(*args, **kwargs)
            self.record_success()
            return res
        except Exception as exc:
            self.record_failure(exc)
            raise

DEFAULT_MODEL_PRICING_USD_PER_1K: Dict[str, float] = {
    "deepseek-chat": 0.0002,
    "deepseek-v3": 0.0002,
    "deepseek-v4-flash": 0.00015,
    "gpt-4o": 0.005,
    "gpt-4o-mini": 0.00015,
    "qwen": 0.0003,
    "default": 0.001
}

@dataclass
class RunContext:
    """
    Global request/task execution context carried across all pipeline stages.
    Provides quota enforcement, stage timeout tracking, and operational observability.
    """
    topic: str
    run_id: str = field(default_factory=lambda: f"run_{uuid.uuid4().hex[:8]}")
    session_id: Optional[str] = None
    llm_config: Dict[str, Any] = field(default_factory=dict)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    telemetry: RunTelemetry = field(default_factory=RunTelemetry)
    circuit_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    stage_timeouts: Dict[str, float] = field(default_factory=lambda: {
        "stage_1_contract": 30.0,
        "stage_2_research": 120.0,
        "stage_3_elaboration": 90.0,
        "stage_4_verification": 60.0,
    })

    def track_cost(self, amount_usd: float):
        """Track incurred monetary cost in USD, raising BudgetExceededError if over budget."""
        if amount_usd <= 0:
            return
        new_total = round(self.telemetry.cost_used_usd + amount_usd, 6)
        if new_total > self.budget.max_cost_usd:
            raise BudgetExceededError("cost_usd", new_total, self.budget.max_cost_usd)
        self.telemetry.cost_used_usd = new_total

    def track_tokens(self, count: int, model_name: Optional[str] = None):
        """Track consumed token count and enforce cost, raising BudgetExceededError if over budget."""
        if count <= 0:
            return
        new_total = self.telemetry.tokens_used + count
        if new_total > self.budget.max_tokens:
            raise BudgetExceededError("tokens", new_total, self.budget.max_tokens)
        self.telemetry.tokens_used = new_total

        # Calculate and enforce estimated cost
        model_key = (model_name or self.llm_config.get("model") or "default").lower()
        pricing = DEFAULT_MODEL_PRICING_USD_PER_1K.get("default", 0.001)
        for k, p in DEFAULT_MODEL_PRICING_USD_PER_1K.items():
            if k in model_key:
                pricing = p
                break
        estimated_cost = (count / 1000.0) * pricing
        self.track_cost(estimated_cost)

    def track_search_call(self):
        """Track web search invocation, raising BudgetExceededError if over budget."""
        new_calls = self.telemetry.search_calls_used + 1
        if new_calls > self.budget.max_search_calls:
            raise BudgetExceededError("search_calls", new_calls, self.budget.max_search_calls)
        self.telemetry.search_calls_used = new_calls

    def record_stage_start(self, stage_name: str):
        self.telemetry.record_stage_start(stage_name)

    def record_stage_end(self, stage_name: str, status: str = "completed"):
        self.telemetry.record_stage_end(stage_name, status)

    def record_error(self, stage_name: str, error: Exception):
        self.telemetry.record_error(stage_name, error)

    def export_summary(self) -> Dict[str, Any]:
        """Export comprehensive execution telemetry summary."""
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "topic": self.topic,
            "tokens_used": self.telemetry.tokens_used,
            "cost_used_usd": round(self.telemetry.cost_used_usd, 6),
            "search_calls_used": self.telemetry.search_calls_used,
            "retry_counts": self.telemetry.retry_counts,
            "stage_durations": self.telemetry.stage_durations,
            "stage_status": self.telemetry.stage_status,
            "circuit_breaker_state": self.circuit_breaker.state,
            "error_count": len(self.telemetry.errors)
        }
