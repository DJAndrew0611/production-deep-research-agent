import unittest
import os
import shutil
import tempfile
import json
from schemas import Claim, EvidenceSource, VerificationResult, ReportRevision, EvidenceLedger
import storage

class TestReportVersionsAndVerification(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test_versions.db")
        storage.init_db(self.test_db)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_verification_result_schema_forbid_extra(self):
        """VerificationResult must reject unknown extra fields."""
        with self.assertRaises(Exception):
            VerificationResult(
                claim_id="claim_01",
                verdict="verified",
                confidence=0.9,
                bogus_extra="invalid"
            )

        vr = VerificationResult(
            claim_id="claim_01",
            verdict="verified",
            confidence=0.95,
            supporting_evidence_ids=["ev_1"],
            reasoning_summary="Cross-checked with first-party docs"
        )
        self.assertEqual(vr.verdict, "verified")
        self.assertEqual(vr.supporting_evidence_ids, ["ev_1"])

    def test_session_creation_generates_revision_1(self):
        """Saving a new session must atomically record Revision 1 in report_versions table."""
        session_data = {
            "session_id": "sess_rev_001",
            "topic": "Quantum Computing 2025",
            "final_report": "# Initial Report v1\nQuantum computing is progressing.",
            "sources": [{"url": "https://example.com/qc", "title": "QC Progress"}],
            "provider": "DeepSeek",
            "model": "deepseek-chat"
        }
        storage.save_session(session_data, db_path=self.test_db)

        # Query revisions
        revisions = storage.list_report_revisions("sess_rev_001", db_path=self.test_db)
        self.assertEqual(len(revisions), 1)
        rev1 = revisions[0]
        self.assertEqual(rev1["revision_no"], 1)
        self.assertIsNone(rev1["parent_revision_id"])
        self.assertIn("Initial Report v1", rev1["final_report"])

    def test_append_feedback_creates_revision_2_with_parent_pointer(self):
        """Appending user feedback must atomically create Revision 2 pointing to Revision 1."""
        session_data = {
            "session_id": "sess_rev_002",
            "topic": "Autonomous Driving",
            "final_report": "# Version 1\nLevel 3 deployed.",
            "sources": []
        }
        storage.save_session(session_data, db_path=self.test_db)

        # Append feedback
        storage.append_feedback_to_session(
            session_id="sess_rev_002",
            user_request="Please clarify safety disengagement metrics.",
            target_agent="Verification Agent",
            revised_report="# Version 2\nLevel 3 deployed. Safety metrics added: 0.1 disengagements/10k miles.",
            db_path=self.test_db
        )

        revisions = storage.list_report_revisions("sess_rev_002", db_path=self.test_db)
        self.assertEqual(len(revisions), 2)
        
        rev1 = [r for r in revisions if r["revision_no"] == 1][0]
        rev2 = [r for r in revisions if r["revision_no"] == 2][0]

        self.assertEqual(rev2["parent_revision_id"], rev1["revision_id"])
        self.assertIn("Safety metrics added", rev2["final_report"])
        self.assertEqual(rev2["change_reason"], "User feedback revision: Please clarify safety disengagement metrics.")

    def test_rollback_to_historical_revision(self):
        """Rolling back to Revision 1 generates Revision 3 restoring Revision 1 report and ledger."""
        session_data = {
            "session_id": "sess_rev_003",
            "topic": "Space Exploration",
            "final_report": "Original Clean Report v1",
            "sources": []
        }
        storage.save_session(session_data, db_path=self.test_db)

        # Mutate to v2
        storage.append_feedback_to_session(
            session_id="sess_rev_003",
            user_request="Add unverified rumor",
            target_agent="Research Agent",
            revised_report="Corrupted Report with Rumor v2",
            db_path=self.test_db
        )

        revisions = storage.list_report_revisions("sess_rev_003", db_path=self.test_db)
        rev1 = [r for r in revisions if r["revision_no"] == 1][0]

        # Rollback to rev1
        restored_rev = storage.rollback_to_revision(
            session_id="sess_rev_003",
            target_revision_id=rev1["revision_id"],
            db_path=self.test_db
        )
        self.assertIsNotNone(restored_rev)
        self.assertEqual(restored_rev["revision_no"], 3)
        self.assertEqual(restored_rev["final_report"], "Original Clean Report v1")
        self.assertIn("Rollback to revision 1", restored_rev["change_reason"])

        # Check current session final_report is updated
        current_sess = storage.get_session("sess_rev_003", db_path=self.test_db)
        self.assertEqual(current_sess["final_report"], "Original Clean Report v1")

    def test_verdict_diff_calculation_and_persistence(self):
        """Verify that append_feedback_to_session calculates and records accurate verdict transitions."""
        claim_stmt = "Model X supports 100k context window"
        v1_claim = Claim(
            statement=claim_stmt,
            status="disputed",
            confidence=0.4
        )
        session_data = {
            "session_id": "sess_rev_diff",
            "topic": "Context Length Verification",
            "final_report": "# Initial: Context is disputed.",
            "sources": [],
            "evidence_ledger": EvidenceLedger(claims=[v1_claim])
        }
        storage.save_session(session_data, db_path=self.test_db)

        # Revision 2: provide new verified claim status
        v2_claim = Claim(
            statement=claim_stmt,
            status="corroborated",
            confidence=0.95
        )
        storage.append_feedback_to_session(
            session_id="sess_rev_diff",
            user_request="Official benchmark docs confirm 100k context window.",
            target_agent="Verification Agent",
            revised_report="# Revised: Context verified at 100k.",
            new_ledger=EvidenceLedger(claims=[v2_claim]),
            db_path=self.test_db
        )

        revisions = storage.list_report_revisions("sess_rev_diff", db_path=self.test_db)
        self.assertEqual(len(revisions), 2)
        rev2 = revisions[1]
        diff = rev2.get("verdict_diff", {})
        self.assertTrue(diff.get("has_changes"))
        self.assertEqual(len(diff.get("changes", [])), 1)
        transition = diff["changes"][0]
        self.assertEqual(transition["statement"], claim_stmt)
        self.assertEqual(transition["old_status"], "disputed")
        self.assertEqual(transition["new_status"], "corroborated")
        self.assertEqual(transition["type"], "transition")


if __name__ == "__main__":
    unittest.main()
