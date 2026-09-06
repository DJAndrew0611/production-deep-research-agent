import json
import os
import sqlite3
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from schemas import (
    Claim, EvidenceSource, EvidenceLedger, Scope, ValidTime,
    QuarantineCandidate, ProductionMemory, ReportRevision, VerificationResult
)

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
DB_PATH = os.path.join(DB_DIR, 'deep_research.db')
LEGACY_HISTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research_history')

def ensure_db_dir() -> str:
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR, exist_ok=True)
    return DB_DIR

def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Get SQLite connection configured with WAL mode and row factory."""
    ensure_db_dir()
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn

def init_db(db_path: Optional[str] = None):
    """Initialize database tables with WAL mode and run migrations."""
    conn = get_connection(db_path)
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            provider TEXT,
            model TEXT,
            initial_report TEXT,
            enhanced_report TEXT,
            verified_report TEXT,
            final_report TEXT,
            sources_json TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS feedback_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            user_request TEXT NOT NULL,
            target_agent TEXT NOT NULL,
            revised_report TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS evidence_ledger (
            claim_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            statement TEXT NOT NULL,
            claim_type TEXT,
            scope_json TEXT,
            valid_time_json TEXT,
            status TEXT,
            confidence REAL,
            evidence_json TEXT,
            notes TEXT,
            created_at TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS memory_quarantine (
            candidate_id TEXT PRIMARY KEY,
            session_id TEXT,
            rule_type TEXT NOT NULL,
            domain TEXT NOT NULL,
            pattern TEXT NOT NULL,
            proposed_action TEXT NOT NULL,
            status TEXT DEFAULT 'quarantined',
            prompt_injection_scan TEXT DEFAULT 'passed',
            review_notes TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS production_memories (
            memory_id TEXT PRIMARY KEY,
            candidate_id TEXT,
            domain TEXT NOT NULL,
            rule_type TEXT NOT NULL,
            pattern TEXT NOT NULL,
            approved_action TEXT NOT NULL,
            version TEXT DEFAULT 'v1.0',
            eval_score REAL,
            created_at TEXT,
            promoted_at TEXT,
            rollback_reason TEXT,
            is_active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS report_versions (
            revision_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            parent_revision_id TEXT,
            revision_no INTEGER NOT NULL,
            initial_report TEXT,
            enhanced_report TEXT,
            verified_report TEXT,
            final_report TEXT NOT NULL,
            ledger_snapshot_json TEXT NOT NULL,
            verification_snapshot_json TEXT,
            verdict_diff_json TEXT DEFAULT '{}',
            change_reason TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_report_versions_session ON report_versions(session_id, revision_no);
        """)

        # Migration helper: ensure new columns exist
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(production_memories);")
        col_names = [r[1] for r in cur.fetchall()]
        if "eval_score" not in col_names:
            conn.execute("ALTER TABLE production_memories ADD COLUMN eval_score REAL;")
        if "promoted_at" not in col_names:
            conn.execute("ALTER TABLE production_memories ADD COLUMN promoted_at TEXT;")
        if "rollback_reason" not in col_names:
            conn.execute("ALTER TABLE production_memories ADD COLUMN rollback_reason TEXT;")

        cur.execute("PRAGMA table_info(report_versions);")
        rv_cols = [r[1] for r in cur.fetchall()]
        if "verdict_diff_json" not in rv_cols:
            conn.execute("ALTER TABLE report_versions ADD COLUMN verdict_diff_json TEXT DEFAULT '{}';")

    conn.close()

# Auto-initialize DB on module load
init_db()

# ----------------- Session Operations -----------------

def save_session(session_data: Dict[str, Any], db_path: Optional[str] = None) -> str:
    """Save or update research session."""
    conn = get_connection(db_path)
    session_id = session_data.get('session_id')
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if not session_id:
        rand_suffix = uuid.uuid4().hex[:6]
        time_part = datetime.now().strftime('%Y%m%d_%H%M%S')
        session_id = f"{time_part}_{rand_suffix}"
        session_data['session_id'] = session_id
        session_data['created_at'] = session_data.get('created_at') or now_str

    created_at = session_data.get('created_at') or now_str
    session_data['updated_at'] = now_str

    sources = session_data.get('sources')
    sources_json = json.dumps(sources, ensure_ascii=False) if sources else "[]"

    with conn:
        conn.execute("""
            INSERT INTO sessions (
                session_id, topic, provider, model, initial_report,
                enhanced_report, verified_report, final_report,
                sources_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                topic=excluded.topic,
                provider=excluded.provider,
                model=excluded.model,
                initial_report=excluded.initial_report,
                enhanced_report=excluded.enhanced_report,
                verified_report=excluded.verified_report,
                final_report=excluded.final_report,
                sources_json=excluded.sources_json,
                updated_at=excluded.updated_at;
        """, (
            session_id,
            session_data.get('topic', 'Untitled Research'),
            session_data.get('provider', ''),
            session_data.get('model', ''),
            session_data.get('initial_report', ''),
            session_data.get('enhanced_report', ''),
            session_data.get('verified_report', ''),
            session_data.get('final_report', ''),
            sources_json,
            created_at,
            now_str
        ))

        # Atomically record Revision 1 if this is the first save of the session
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM report_versions WHERE session_id = ?", (session_id,))
        if cur.fetchone()[0] == 0 and session_data.get('final_report'):
            rev_id = f"rev_{uuid.uuid4().hex[:8]}"
            ledger_snapshot = "{}"
            if 'evidence_ledger' in session_data:
                el = session_data['evidence_ledger']
                if hasattr(el, 'model_dump_json'):
                    ledger_snapshot = el.model_dump_json()
                elif isinstance(el, dict):
                    ledger_snapshot = json.dumps(el, ensure_ascii=False)

            conn.execute("""
                INSERT INTO report_versions (
                    revision_id, session_id, parent_revision_id, revision_no,
                    initial_report, enhanced_report, verified_report, final_report,
                    ledger_snapshot_json, verification_snapshot_json, change_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                rev_id, session_id, None, 1,
                session_data.get('initial_report', ''),
                session_data.get('enhanced_report', ''),
                session_data.get('verified_report', ''),
                session_data.get('final_report', ''),
                ledger_snapshot,
                session_data.get('verification_snapshot_json'),
                "Initial generation",
                now_str
            ))
    conn.close()

    # If evidence_ledger is provided, save it
    if 'evidence_ledger' in session_data:
        ledger_data = session_data['evidence_ledger']
        if isinstance(ledger_data, EvidenceLedger):
            save_evidence_ledger(session_id, ledger_data, db_path)
        elif isinstance(ledger_data, dict):
            save_evidence_ledger(session_id, EvidenceLedger.model_validate(ledger_data), db_path)

    return session_id

def get_session(session_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve full session data including feedback history and evidence ledger."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None

    session_dict = dict(row)
    try:
        session_dict['sources'] = json.loads(session_dict.get('sources_json') or '[]')
    except Exception:
        session_dict['sources'] = []

    # Fetch feedback history
    cur.execute("SELECT timestamp, user_request, target_agent, revised_report FROM feedback_history WHERE session_id = ? ORDER BY id ASC", (session_id,))
    feedback_rows = cur.fetchall()
    session_dict['feedback_history'] = [dict(fb) for fb in feedback_rows]

    conn.close()

    # Attach evidence ledger if exists
    ledger = get_evidence_ledger(session_id, db_path)
    if ledger:
        session_dict['evidence_ledger'] = ledger.model_dump()

    return session_dict

def list_sessions(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """List summary for all saved sessions sorted by updated_at descending."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT s.session_id, s.topic, s.created_at, s.updated_at, s.provider, s.model,
               COUNT(f.id) as feedback_count
        FROM sessions s
        LEFT JOIN feedback_history f ON s.session_id = f.session_id
        GROUP BY s.session_id
        ORDER BY s.updated_at DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_session(session_id: str, db_path: Optional[str] = None) -> bool:
    """Delete session and cascade associated records."""
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM feedback_history WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM evidence_ledger WHERE session_id = ?", (session_id,))
            deleted = cur.rowcount > 0
        conn.close()
        return deleted
    except Exception as e:
        print(f"Error deleting session {session_id}: {e}")
        conn.close()
        return False

def append_feedback_to_session(
    session_id: str,
    user_request: str,
    target_agent: str,
    revised_report: str,
    new_ledger: Optional[EvidenceLedger] = None,
    verification_results: Optional[List[VerificationResult]] = None,
    db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Record user feedback, update final report, and atomically synchronize evidence ledger and verification results."""
    conn = get_connection(db_path)
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        with conn:
            conn.execute("""
                INSERT INTO feedback_history (session_id, timestamp, user_request, target_agent, revised_report)
                VALUES (?, ?, ?, ?, ?)
            """, (session_id, now_str, user_request, target_agent, revised_report))

            conn.execute("""
                UPDATE sessions SET final_report = ?, updated_at = ? WHERE session_id = ?
            """, (revised_report, now_str, session_id))

            # Fetch latest parent revision to get old verdict snapshot
            cur = conn.cursor()
            cur.execute("""
                SELECT revision_id, revision_no, verification_snapshot_json, ledger_snapshot_json 
                FROM report_versions 
                WHERE session_id = ? 
                ORDER BY revision_no DESC LIMIT 1
            """, (session_id,))
            latest_row = cur.fetchone()
            parent_rev_id = latest_row[0] if latest_row else None
            next_rev_no = (latest_row[1] + 1) if latest_row else 1
            old_verif_json = latest_row[2] if latest_row else None
            old_ledger_json = latest_row[3] if latest_row else None

            # Compute fine-grained Verdict Diff between parent and new revision
            claim_id_to_statement = {}
            if new_ledger and new_ledger.claims:
                for c in new_ledger.claims:
                    if getattr(c, "claim_id", None) and getattr(c, "statement", None):
                        claim_id_to_statement[c.claim_id] = c.statement
            if old_ledger_json:
                try:
                    for c_d in json.loads(old_ledger_json).get("claims", []):
                        if c_d.get("claim_id") and c_d.get("statement"):
                            claim_id_to_statement.setdefault(c_d["claim_id"], c_d["statement"])
                except Exception:
                    pass

            old_statuses = {}
            if old_verif_json:
                try:
                    for v in json.loads(old_verif_json):
                        cid = v.get("claim_id", "")
                        stmt = v.get("statement") or claim_id_to_statement.get(cid, cid)
                        if stmt:
                            old_statuses[stmt] = v.get("verdict", "unknown")
                except Exception:
                    pass
            if old_ledger_json:
                try:
                    old_l_data = json.loads(old_ledger_json)
                    for c_d in old_l_data.get("claims", []):
                        stmt = c_d.get("statement") or claim_id_to_statement.get(c_d.get("claim_id", ""), c_d.get("claim_id", ""))
                        if stmt and stmt not in old_statuses:
                            old_statuses[stmt] = c_d.get("status", "unknown")
                except Exception:
                    pass

            new_statuses = {}
            if verification_results:
                for vr in verification_results:
                    if isinstance(vr, dict):
                        cid = vr.get("claim_id", "")
                        stmt = vr.get("statement") or claim_id_to_statement.get(cid, cid)
                        verdict = vr.get("verdict", "unknown")
                    else:
                        cid = getattr(vr, "claim_id", "")
                        stmt = getattr(vr, "statement", None) or claim_id_to_statement.get(cid, cid)
                        verdict = getattr(vr, "verdict", "unknown")
                    if stmt:
                        new_statuses[stmt] = verdict
            elif new_ledger:
                for c in new_ledger.claims:
                    new_statuses[c.statement] = c.status

            diff_entries = []
            for stmt, new_st in new_statuses.items():
                old_st = old_statuses.get(stmt)
                if old_st and old_st != new_st:
                    diff_entries.append({
                        "statement": stmt,
                        "old_status": old_st,
                        "new_status": new_st,
                        "type": "transition"
                    })
                elif not old_st:
                    diff_entries.append({
                        "statement": stmt,
                        "old_status": "none",
                        "new_status": new_st,
                        "type": "added"
                    })

            verdict_diff = {
                "has_changes": len(diff_entries) > 0,
                "changes": diff_entries,
                "summary": f"共 {len(diff_entries)} 条断言状态发生更迭" if diff_entries else "事实状态保持稳定"
            }
            verdict_diff_json = json.dumps(verdict_diff, ensure_ascii=False)

            # Synchronize evidence ledger if new_ledger is provided
            if new_ledger is not None and new_ledger.claims:
                conn.execute("DELETE FROM evidence_ledger WHERE session_id = ?", (session_id,))
                for c in new_ledger.claims:
                    scope_json = json.dumps(c.scope.model_dump() if c.scope else {}, ensure_ascii=False)
                    valid_time_json = json.dumps(c.valid_time.model_dump() if c.valid_time else {}, ensure_ascii=False)
                    evidence_json = json.dumps([ev.model_dump() for ev in c.evidence], ensure_ascii=False)
                    conn.execute("""
                        INSERT OR REPLACE INTO evidence_ledger (
                            claim_id, session_id, statement, claim_type, scope_json,
                            valid_time_json, status, confidence, evidence_json, notes, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        c.claim_id, session_id, c.statement, c.claim_type,
                        scope_json, valid_time_json, c.status,
                        float(c.confidence), evidence_json, c.notes, now_str
                    ))
                ledger_snapshot_json = json.dumps({
                    "claims": [c.model_dump() for c in new_ledger.claims],
                    "gaps_identified": new_ledger.gaps_identified
                }, ensure_ascii=False)
            else:
                cur.execute("SELECT claim_id, statement, claim_type, scope_json, valid_time_json, status, confidence, evidence_json, notes FROM evidence_ledger WHERE session_id = ?", (session_id,))
                claim_rows = cur.fetchall()
                claims_data = []
                for cr in claim_rows:
                    claims_data.append({
                        "claim_id": cr[0],
                        "statement": cr[1],
                        "claim_type": cr[2],
                        "scope": json.loads(cr[3] or "{}"),
                        "valid_time": json.loads(cr[4] or "{}"),
                        "status": cr[5],
                        "confidence": cr[6],
                        "evidence": json.loads(cr[7] or "[]"),
                        "notes": cr[8]
                    })
                ledger_snapshot_json = json.dumps({"claims": claims_data}, ensure_ascii=False)

            verif_snapshot_json = None
            if verification_results:
                verif_snapshot_json = json.dumps([v.model_dump() for v in verification_results], ensure_ascii=False)

            new_rev_id = f"rev_{uuid.uuid4().hex[:8]}"
            conn.execute("""
                INSERT INTO report_versions (
                    revision_id, session_id, parent_revision_id, revision_no,
                    initial_report, enhanced_report, verified_report, final_report,
                    ledger_snapshot_json, verification_snapshot_json, verdict_diff_json, change_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                new_rev_id, session_id, parent_rev_id, next_rev_no,
                "", "", "", revised_report,
                ledger_snapshot_json, verif_snapshot_json, verdict_diff_json,
                f"User feedback revision: {user_request}",
                now_str
            ))

        conn.close()
        return get_session(session_id, db_path)
    except Exception as e:
        print(f"Error appending feedback to {session_id}: {e}")
        conn.close()
        return None

# ----------------- Report Versioning Operations -----------------

def list_report_revisions(session_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all report revisions for a session sorted by revision_no ascending with parsed verdict_diff."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM report_versions WHERE session_id = ? ORDER BY revision_no ASC", (session_id,))
    rows = cur.fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d["verdict_diff"] = json.loads(d.get("verdict_diff_json") or "{}")
        except Exception:
            d["verdict_diff"] = {}
        results.append(d)
    return results

def get_report_revision(revision_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve full details of a specific report revision."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM report_versions WHERE revision_id = ?", (revision_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def rollback_to_revision(
    session_id: str,
    target_revision_id: str,
    reason: Optional[str] = None,
    db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Rollback to a historical report revision.
    Atomically inserts a new revision pointing back to target and restores session final_report and ledger.
    """
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM report_versions WHERE revision_id = ? AND session_id = ?", (target_revision_id, session_id))
    target_row = cur.fetchone()
    if not target_row:
        conn.close()
        return None

    target_rev = dict(target_row)
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cur.execute("SELECT revision_no FROM report_versions WHERE session_id = ? ORDER BY revision_no DESC LIMIT 1", (session_id,))
    latest_no = cur.fetchone()[0]
    next_no = latest_no + 1

    new_rev_id = f"rev_{uuid.uuid4().hex[:8]}"
    change_reason = reason or f"Rollback to revision {target_rev['revision_no']} ({target_rev['revision_id']})"

    try:
        with conn:
            conn.execute("""
                INSERT INTO report_versions (
                    revision_id, session_id, parent_revision_id, revision_no,
                    initial_report, enhanced_report, verified_report, final_report,
                    ledger_snapshot_json, verification_snapshot_json, change_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                new_rev_id, session_id, target_revision_id, next_no,
                target_rev.get('initial_report', ''),
                target_rev.get('enhanced_report', ''),
                target_rev.get('verified_report', ''),
                target_rev['final_report'],
                target_rev['ledger_snapshot_json'],
                target_rev.get('verification_snapshot_json'),
                change_reason,
                now_str
            ))

            conn.execute("""
                UPDATE sessions SET final_report = ?, updated_at = ? WHERE session_id = ?
            """, (target_rev['final_report'], now_str, session_id))

            # Restore evidence ledger
            try:
                ledger_data = json.loads(target_rev['ledger_snapshot_json'])
                if ledger_data and "claims" in ledger_data:
                    conn.execute("DELETE FROM evidence_ledger WHERE session_id = ?", (session_id,))
                    for c in ledger_data.get("claims", []):
                        scope_json = json.dumps(c.get("scope", {}), ensure_ascii=False)
                        valid_time_json = json.dumps(c.get("valid_time", {}), ensure_ascii=False)
                        evidence_json = json.dumps(c.get("evidence", []), ensure_ascii=False)
                        conn.execute("""
                            INSERT OR REPLACE INTO evidence_ledger (
                                claim_id, session_id, statement, claim_type, scope_json,
                                valid_time_json, status, confidence, evidence_json, notes, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            c.get("claim_id", f"claim_{uuid.uuid4().hex[:8]}"),
                            session_id, c.get("statement", ""), c.get("claim_type", "general_fact"),
                            scope_json, valid_time_json, c.get("status", "unverified"),
                            float(c.get("confidence", 0.5)), evidence_json, c.get("notes"), now_str
                        ))
            except Exception as e:
                print(f"Warning: could not restore ledger snapshot on rollback: {e}")

        conn.close()
        return get_report_revision(new_rev_id, db_path)
    except Exception as e:
        print(f"Error rolling back to revision {target_revision_id}: {e}")
        conn.close()
        return None

# ----------------- Evidence Ledger Operations -----------------

def save_evidence_ledger(session_id: str, ledger: EvidenceLedger, db_path: Optional[str] = None):
    """Persist structured claims and evidence for a session."""
    conn = get_connection(db_path)
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    with conn:
        for claim in ledger.claims:
            scope_json = json.dumps(claim.scope.model_dump() if claim.scope else {}, ensure_ascii=False)
            valid_time_json = json.dumps(claim.valid_time.model_dump() if claim.valid_time else {}, ensure_ascii=False)
            evidence_json = json.dumps([ev.model_dump() for ev in claim.evidence], ensure_ascii=False)

            conn.execute("""
                INSERT INTO evidence_ledger (
                    claim_id, session_id, statement, claim_type, scope_json,
                    valid_time_json, status, confidence, evidence_json, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(claim_id) DO UPDATE SET
                    statement=excluded.statement,
                    claim_type=excluded.claim_type,
                    scope_json=excluded.scope_json,
                    valid_time_json=excluded.valid_time_json,
                    status=excluded.status,
                    confidence=excluded.confidence,
                    evidence_json=excluded.evidence_json,
                    notes=excluded.notes;
            """, (
                claim.claim_id,
                session_id,
                claim.statement,
                claim.claim_type,
                scope_json,
                valid_time_json,
                claim.status,
                claim.confidence,
                evidence_json,
                claim.notes,
                now_str
            ))
    conn.close()

def get_evidence_ledger(session_id: str, db_path: Optional[str] = None) -> Optional[EvidenceLedger]:
    """Load and reconstruct EvidenceLedger for a session."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM evidence_ledger WHERE session_id = ?", (session_id,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return None

    claims = []
    gaps = []
    for r in rows:
        r_dict = dict(r)
        scope_obj = Scope.model_validate(json.loads(r_dict.get('scope_json') or '{}'))
        valid_time_obj = ValidTime.model_validate(json.loads(r_dict.get('valid_time_json') or '{}'))
        ev_list = []
        for e in json.loads(r_dict.get('evidence_json') or '[]'):
            try:
                ev_list.append(EvidenceSource.model_validate(e))
            except Exception:
                continue

        claim = Claim(
            claim_id=r_dict['claim_id'],
            statement=r_dict['statement'],
            claim_type=r_dict.get('claim_type', 'general_fact'),
            scope=scope_obj,
            valid_time=valid_time_obj,
            status=r_dict.get('status', 'unverified'),
            evidence=ev_list,
            confidence=float(r_dict.get('confidence', 0.5)),
            notes=r_dict.get('notes')
        )
        claims.append(claim)
        if claim.status in ["disputed", "unverified", "extracted"]:
            gaps.append(f"Claim [{claim.claim_id}] status is {claim.status}: {claim.statement[:50]}...")

    return EvidenceLedger(claims=claims, gaps_identified=gaps)

# ----------------- Memory Quarantine Operations -----------------

def add_quarantine_candidate(
    pattern: str,
    proposed_action: str,
    domain: str = "General",
    rule_type: str = "mistake_avoidance",
    session_id: Optional[str] = None,
    prompt_injection_scan: str = "passed",
    review_notes: Optional[str] = None,
    db_path: Optional[str] = None
) -> QuarantineCandidate:
    """Add a reflection or user feedback correction into the quarantine area."""
    candidate = QuarantineCandidate(
        session_id=session_id,
        rule_type=rule_type,
        domain=domain,
        pattern=pattern,
        proposed_action=proposed_action,
        status="quarantined",
        prompt_injection_scan=prompt_injection_scan,
        review_notes=review_notes
    )

    conn = get_connection(db_path)
    with conn:
        conn.execute("""
            INSERT INTO memory_quarantine (
                candidate_id, session_id, rule_type, domain, pattern,
                proposed_action, status, prompt_injection_scan, review_notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            candidate.candidate_id,
            candidate.source_task_id or session_id,
            candidate.rule_type,
            candidate.domain,
            candidate.pattern,
            candidate.proposed_action,
            candidate.status,
            candidate.prompt_injection_scan,
            candidate.review_notes,
            candidate.created_at
        ))
    conn.close()
    return candidate

def list_quarantine_candidates(status: Optional[str] = None, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """List quarantine candidates, optionally filtered by status."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    if status:
        cur.execute("SELECT * FROM memory_quarantine WHERE status = ? ORDER BY created_at DESC", (status,))
    else:
        cur.execute("SELECT * FROM memory_quarantine ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def promote_candidate(
    candidate_id: str,
    approved_action: Optional[str] = None,
    db_path: Optional[str] = None,
    enforce_gate: bool = True
) -> Tuple[Optional[ProductionMemory], str]:
    """
    Promote a quarantined candidate to production memory after verification/approval.
    If enforce_gate is True, executes the evaluation benchmark runner against the candidate.
    """
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM memory_quarantine WHERE candidate_id = ?", (candidate_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None, "Candidate not found in quarantine"

    cand_dict = dict(row)
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    final_action = approved_action or cand_dict['proposed_action']

    eval_score = 1.0
    if enforce_gate:
        try:
            from evaluation.runner import run_offline_benchmark
            bench_res = run_offline_benchmark(verbose=False, test_candidate=cand_dict)
            if not bench_res.get("gate_passed"):
                reasons = bench_res.get("negative_transfer_reasons", []) or ["Benchmark gate criteria not met"]
                failure_msg = f"Gate Blocked: {', '.join(reasons)}"
                with conn:
                    conn.execute("UPDATE memory_quarantine SET review_notes = ? WHERE candidate_id = ?", (failure_msg, candidate_id))
                conn.close()
                return None, failure_msg
            eval_score = bench_res.get("avg_claim_precision", 1.0)
        except Exception as e:
            failure_msg = f"Gate Execution Error: {str(e)}"
            conn.close()
            return None, failure_msg

    # Determine version tag based on domain count
    cur.execute("SELECT COUNT(*) FROM production_memories WHERE domain = ?", (cand_dict['domain'],))
    domain_count = cur.fetchone()[0]
    version = f"v1.{domain_count + 1}"

    prod_mem = ProductionMemory(
        candidate_id=candidate_id,
        domain=cand_dict['domain'],
        rule_type=cand_dict['rule_type'],
        pattern=cand_dict['pattern'],
        approved_action=final_action,
        version=version,
        eval_score=eval_score,
        created_at=now_str,
        promoted_at=now_str,
        is_active=True
    )

    with conn:
        conn.execute("""
            INSERT INTO production_memories (
                memory_id, candidate_id, domain, rule_type, pattern,
                approved_action, version, eval_score, created_at, promoted_at, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            prod_mem.memory_id,
            prod_mem.candidate_id,
            prod_mem.domain,
            prod_mem.rule_type,
            prod_mem.pattern,
            prod_mem.approved_action,
            prod_mem.version,
            prod_mem.eval_score,
            prod_mem.created_at,
            prod_mem.promoted_at
        ))
        conn.execute("UPDATE memory_quarantine SET status = 'promoted', review_notes = 'Passed benchmark regression gate' WHERE candidate_id = ?", (candidate_id,))
    conn.close()
    return prod_mem, "Success"

def rollback_memory(memory_id: str, reason: str = "Rollback requested by operator", db_path: Optional[str] = None) -> bool:
    """Deactivate an active production memory and update quarantine status."""
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute("SELECT candidate_id FROM production_memories WHERE memory_id = ?", (memory_id,))
            row = cur.fetchone()
            cand_id = row[0] if row else None

            conn.execute(
                "UPDATE production_memories SET is_active = 0, rollback_reason = ? WHERE memory_id = ?",
                (reason, memory_id)
            )
            if cand_id:
                conn.execute(
                    "UPDATE memory_quarantine SET status = 'rolled_back', review_notes = ? WHERE candidate_id = ?",
                    (f"Rolled back: {reason}", cand_id)
                )
        conn.close()
        return True
    except Exception as e:
        print(f"Error rolling back memory {memory_id}: {e}")
        conn.close()
        return False

def reject_candidate(candidate_id: str, notes: Optional[str] = None, db_path: Optional[str] = None) -> bool:
    """Mark a quarantined candidate as rejected."""
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE memory_quarantine SET status = 'rejected', review_notes = ? WHERE candidate_id = ?",
                (notes, candidate_id)
            )
            rejected = cur.rowcount > 0
        conn.close()
        return rejected
    except Exception:
        conn.close()
        return False

def list_production_memories(active_only: bool = True, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve all approved production memories."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    if active_only:
        cur.execute("SELECT * FROM production_memories WHERE is_active = 1 ORDER BY created_at DESC")
    else:
        cur.execute("SELECT * FROM production_memories ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def toggle_production_memory(memory_id: str, is_active: bool, db_path: Optional[str] = None) -> bool:
    """Enable or disable a production memory."""
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute("UPDATE production_memories SET is_active = ? WHERE memory_id = ?", (1 if is_active else 0, memory_id))
            updated = cur.rowcount > 0
        conn.close()
        return updated
    except Exception:
        conn.close()
        return False

# ----------------- Migration & Export -----------------

def auto_migrate_legacy_json(db_path: Optional[str] = None) -> int:
    """Scan legacy research_history/ directory and import sessions into SQLite."""
    if not os.path.exists(LEGACY_HISTORY_DIR):
        return 0

    migrated_count = 0
    conn = get_connection(db_path)
    cur = conn.cursor()

    for filename in os.listdir(LEGACY_HISTORY_DIR):
        if filename.endswith('.json'):
            file_path = os.path.join(LEGACY_HISTORY_DIR, filename)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                session_id = data.get('session_id') or filename[:-5]
                cur.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,))
                if cur.fetchone():
                    continue

                data['session_id'] = session_id
                save_session(data, db_path)

                feedbacks = data.get('feedback_history', [])
                for fb in feedbacks:
                    append_feedback_to_session(
                        session_id=session_id,
                        user_request=fb.get('user_request', ''),
                        target_agent=fb.get('target_agent', ''),
                        revised_report=fb.get('revised_report', ''),
                        db_path=db_path
                    )
                migrated_count += 1
            except Exception as e:
                print(f"Error migrating {filename}: {e}")
                continue

    conn.close()
    return migrated_count

def export_session(session_id: str, format_type: str = "markdown", db_path: Optional[str] = None) -> str:
    """Export session as formatted Markdown or full JSON snapshot."""
    session = get_session(session_id, db_path)
    if not session:
        return ""

    if format_type.lower() == "json":
        return json.dumps(session, ensure_ascii=False, indent=2)

    lines = [
        f"# 研究报告：{session.get('topic')}",
        f"**生成时间**: {session.get('created_at')} | **更新时间**: {session.get('updated_at')}",
        f"**大模型供应商**: {session.get('provider')} ({session.get('model')})",
        "\n---\n",
        session.get('final_report', '')
    ]
    return "\n".join(lines)
