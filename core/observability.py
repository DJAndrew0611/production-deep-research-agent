"""
Structured logging and observability for the Deep Research Agent.
Provides JSON-formatted structured logs with trace context propagation.
"""
import logging
import json
import time
import uuid
from typing import Optional, Dict, Any
from functools import wraps
from core.context import RunContext


class StructuredFormatter(logging.Formatter):
    """JSON structured log formatter for production observability."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Attach structured context if available
        if hasattr(record, "run_id"):
            log_entry["run_id"] = record.run_id
        if hasattr(record, "stage"):
            log_entry["stage"] = record.stage
        if hasattr(record, "session_id"):
            log_entry["session_id"] = record.session_id
        if hasattr(record, "duration_ms"):
            log_entry["duration_ms"] = record.duration_ms
        if hasattr(record, "extra_data"):
            log_entry["data"] = record.extra_data

        # Attach exception info
        if record.exc_info and record.exc_info[1]:
            log_entry["error"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": str(record.exc_info[1]),
            }

        return json.dumps(log_entry, ensure_ascii=False, default=str)


def setup_logger(
    name: str = "deep_research",
    level: int = logging.INFO,
    log_file: Optional[str] = None
) -> logging.Logger:
    """
    Configure and return a structured JSON logger.
    
    Args:
        name: Logger name
        level: Logging level
        log_file: Optional file path for persistent logs
    
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    
    # Avoid adding duplicate handlers
    if logger.handlers:
        return logger
    
    logger.setLevel(level)
    formatter = StructuredFormatter()

    # Console handler (always)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


class StageTracer:
    """
    Context manager for tracing pipeline stage execution.
    Emits structured start/end/error logs with duration.
    
    Usage:
        async with StageTracer(logger, context, "stage_2_research"):
            result = await do_research()
    """

    def __init__(
        self,
        logger: logging.Logger,
        context: RunContext,
        stage_name: str
    ):
        self.logger = logger
        self.context = context
        self.stage_name = stage_name
        self.start_time: float = 0

    async def __aenter__(self):
        self.start_time = time.time()
        self.context.record_stage_start(self.stage_name)
        self.logger.info(
            f"Stage started: {self.stage_name}",
            extra={
                "run_id": self.context.run_id,
                "stage": self.stage_name,
                "session_id": self.context.session_id,
            }
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        duration_ms = round((time.time() - self.start_time) * 1000, 1)
        if exc_val is not None:
            self.context.record_stage_end(self.stage_name, status="error")
            self.context.record_error(self.stage_name, exc_val)
            self.logger.error(
                f"Stage failed: {self.stage_name}",
                extra={
                    "run_id": self.context.run_id,
                    "stage": self.stage_name,
                    "duration_ms": duration_ms,
                    "extra_data": {"error": str(exc_val)},
                },
                exc_info=(exc_type, exc_val, exc_tb)
            )
        else:
            self.context.record_stage_end(self.stage_name, status="completed")
            self.logger.info(
                f"Stage completed: {self.stage_name}",
                extra={
                    "run_id": self.context.run_id,
                    "stage": self.stage_name,
                    "duration_ms": duration_ms,
                    "extra_data": {
                        "tokens_used": self.context.telemetry.tokens_used,
                        "search_calls_used": self.context.telemetry.search_calls_used,
                    },
                }
            )
        # Don't suppress exceptions
        return False


def log_pipeline_summary(logger: logging.Logger, context: RunContext):
    """Emit a final structured summary log for the entire pipeline run."""
    summary = context.export_summary()
    logger.info(
        "Pipeline execution completed",
        extra={
            "run_id": context.run_id,
            "session_id": context.session_id,
            "extra_data": summary,
        }
    )
