import os
from typing import List, Dict, Any, Optional, Tuple
import storage
from schemas import EvidenceLedger, QuarantineCandidate, ProductionMemory, VerificationResult

# Backwards-compatible directory pointer
HISTORY_DIR = storage.LEGACY_HISTORY_DIR

def _get_active_db_path() -> Optional[str]:
    """Support isolated test directories if HISTORY_DIR was overridden."""
    if HISTORY_DIR != storage.LEGACY_HISTORY_DIR:
        storage.ensure_db_dir()
        if not os.path.exists(HISTORY_DIR):
            os.makedirs(HISTORY_DIR, exist_ok=True)
        test_db = os.path.join(HISTORY_DIR, "test_deep_research.db")
        storage.init_db(test_db)
        return test_db
    return None

# Auto-migrate any existing legacy JSON files on import
try:
    storage.auto_migrate_legacy_json()
except Exception as e:
    print(f"Notice: Legacy JSON migration check skipped or failed: {e}")

def save_session(session_data: Dict[str, Any]) -> str:
    """Save session to SQLite WAL database."""
    return storage.save_session(session_data, db_path=_get_active_db_path())

def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve session from SQLite database."""
    return storage.get_session(session_id, db_path=_get_active_db_path())

def list_sessions() -> List[Dict[str, Any]]:
    """List all sessions ordered by last update descending."""
    return storage.list_sessions(db_path=_get_active_db_path())

def delete_session(session_id: str) -> bool:
    """Delete session and cascade associated records from SQLite."""
    return storage.delete_session(session_id, db_path=_get_active_db_path())

def append_feedback_to_session(
    session_id: str,
    user_request: str,
    target_agent: str,
    revised_report: str,
    new_ledger: Optional[EvidenceLedger] = None,
    verification_results: Optional[List[VerificationResult]] = None
) -> Optional[Dict[str, Any]]:
    """Append feedback and update revised final report in SQLite."""
    return storage.append_feedback_to_session(
        session_id=session_id,
        user_request=user_request,
        target_agent=target_agent,
        revised_report=revised_report,
        new_ledger=new_ledger,
        verification_results=verification_results,
        db_path=_get_active_db_path()
    )

# Expose governance and evidence ledger operations
def save_evidence_ledger(session_id: str, ledger: EvidenceLedger):
    storage.save_evidence_ledger(session_id, ledger, db_path=_get_active_db_path())

def get_evidence_ledger(session_id: str) -> Optional[EvidenceLedger]:
    return storage.get_evidence_ledger(session_id, db_path=_get_active_db_path())

def add_quarantine_candidate(
    pattern: str,
    proposed_action: str,
    domain: str = "General",
    rule_type: str = "mistake_avoidance",
    session_id: Optional[str] = None,
    prompt_injection_scan: str = "passed",
    review_notes: Optional[str] = None
) -> QuarantineCandidate:
    return storage.add_quarantine_candidate(
        pattern=pattern,
        proposed_action=proposed_action,
        domain=domain,
        rule_type=rule_type,
        session_id=session_id,
        prompt_injection_scan=prompt_injection_scan,
        review_notes=review_notes,
        db_path=_get_active_db_path()
    )

def list_quarantine_candidates(status: Optional[str] = None) -> List[Dict[str, Any]]:
    return storage.list_quarantine_candidates(status, db_path=_get_active_db_path())

def promote_candidate(candidate_id: str, approved_action: Optional[str] = None, enforce_gate: bool = True) -> Tuple[Optional[ProductionMemory], str]:
    return storage.promote_candidate(candidate_id, approved_action, db_path=_get_active_db_path(), enforce_gate=enforce_gate)

def rollback_memory(memory_id: str, reason: str = "Rollback requested") -> bool:
    return storage.rollback_memory(memory_id, reason, db_path=_get_active_db_path())

def reject_candidate(candidate_id: str, notes: Optional[str] = None) -> bool:
    return storage.reject_candidate(candidate_id, notes, db_path=_get_active_db_path())

def list_production_memories(active_only: bool = True) -> List[Dict[str, Any]]:
    return storage.list_production_memories(active_only, db_path=_get_active_db_path())

def toggle_production_memory(memory_id: str, is_active: bool) -> bool:
    return storage.toggle_production_memory(memory_id, is_active, db_path=_get_active_db_path())

def export_session(session_id: str, format_type: str = "markdown") -> str:
    return storage.export_session(session_id, format_type, db_path=_get_active_db_path())

def list_report_revisions(session_id: str) -> List[Dict[str, Any]]:
    return storage.list_report_revisions(session_id, db_path=_get_active_db_path())

def get_report_revision(revision_id: str) -> Optional[Dict[str, Any]]:
    return storage.get_report_revision(revision_id, db_path=_get_active_db_path())

def rollback_to_revision(session_id: str, target_revision_id: str, reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return storage.rollback_to_revision(session_id, target_revision_id, reason=reason, db_path=_get_active_db_path())
