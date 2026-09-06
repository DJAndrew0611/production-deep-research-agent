import sys
from typing import Dict, Any, Optional
from evaluation.runner import run_offline_benchmark

def run_benchmark_suite(
    verbose: bool = True,
    test_candidate: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Compatibility wrapper around the evaluation package runner."""
    summary = run_offline_benchmark(verbose=verbose, test_candidate=test_candidate)
    # Map gate_passed and legacy metric names for backward compatibility
    summary["benchmark_passed"] = summary["gate_passed"]
    summary["avg_citation_grounding"] = summary.get("avg_citation_validity", 1.0)
    summary["gap_detection_recall"] = summary.get("avg_gap_detection_recall", 1.0)
    return summary

if __name__ == "__main__":
    summary = run_benchmark_suite(verbose=True)
    if not summary["gate_passed"]:
        sys.exit(1)
    sys.exit(0)
