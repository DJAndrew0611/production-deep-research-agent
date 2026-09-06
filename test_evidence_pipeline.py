import unittest
import os
import shutil
import tempfile
from schemas import (
    Scope, ValidTime, EvidenceSource, Claim, EvidenceLedger,
    QuarantineCandidate, ProductionMemory
)
import storage

class TestEvidencePipeline(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test_deep_research.db")
        storage.init_db(self.test_db)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_claim_evidence_schema(self):
        ev = EvidenceSource(
            url="https://worldlabs.ai/blog/marble",
            title="World Labs Marble Announcement",
            source_type="first_party_announcement",
            published_at="2025-11-12",
            quote_locator="Paragraph 1",
            stance="supports"
        )
        claim = Claim(
            statement="World Labs Marble released in Nov 2025",
            claim_type="product_general_availability",
            scope=Scope(product="Marble", release_stage="GA"),
            valid_time=ValidTime(effective_from="2025-11-12"),
            status="corroborated",
            evidence=[ev],
            confidence=0.98
        )
        ledger = EvidenceLedger(claims=[claim], gaps_identified=[])
        self.assertEqual(len(ledger.get_corroborated_claims()), 1)
        self.assertFalse(ledger.has_critical_gaps())

        # Test gap detection trigger
        claim_disputed = Claim(
            statement="Unverified rumors about 2026 release",
            status="disputed",
            confidence=0.4
        )
        ledger.claims.append(claim_disputed)
        self.assertTrue(ledger.has_critical_gaps())

    def test_storage_and_evidence_ledger_lifecycle(self):
        ev = EvidenceSource(url="https://example.com/test", stance="supports")
        claim = Claim(
            statement="Test assertion",
            scope=Scope(product="TestProd"),
            status="corroborated",
            evidence=[ev]
        )
        ledger = EvidenceLedger(claims=[claim])

        session_data = {
            "topic": "AI Robotics 2026",
            "provider": "DeepSeek",
            "model": "deepseek-chat",
            "initial_report": "Init",
            "enhanced_report": "Enhanced",
            "verified_report": "Verified",
            "final_report": "Final",
            "evidence_ledger": ledger
        }

        # Save session
        session_id = storage.save_session(session_data, db_path=self.test_db)
        self.assertIsNotNone(session_id)

        # Retrieve session
        retrieved = storage.get_session(session_id, db_path=self.test_db)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["topic"], "AI Robotics 2026")
        self.assertIn("evidence_ledger", retrieved)
        self.assertEqual(len(retrieved["evidence_ledger"]["claims"]), 1)
        self.assertEqual(retrieved["evidence_ledger"]["claims"][0]["statement"], "Test assertion")

        # Check evidence ledger direct loader
        loaded_ledger = storage.get_evidence_ledger(session_id, db_path=self.test_db)
        self.assertIsNotNone(loaded_ledger)
        self.assertEqual(len(loaded_ledger.claims), 1)

    def test_memory_quarantine_governance_lifecycle(self):
        # 1. Ingest into quarantine (Must start as quarantined, never directly active)
        candidate = storage.add_quarantine_candidate(
            pattern="Do not confuse 2025 release dates with 2026",
            proposed_action="Check first-party blogs for release dates",
            domain="Timeline",
            rule_type="mistake_avoidance",
            db_path=self.test_db
        )
        self.assertEqual(candidate.status, "quarantined")

        candidates = storage.list_quarantine_candidates(status="quarantined", db_path=self.test_db)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["candidate_id"], candidate.candidate_id)

        # 2. Production memory should be empty before promotion
        active_memories = storage.list_production_memories(active_only=True, db_path=self.test_db)
        self.assertEqual(len(active_memories), 0)

        # 3. Promote candidate
        promoted_mem, msg = storage.promote_candidate(candidate.candidate_id, db_path=self.test_db)
        self.assertIsNotNone(promoted_mem, f"Promotion failed: {msg}")
        self.assertEqual(promoted_mem.pattern, candidate.pattern)

        # 4. Check that it is now in production memories and status is 'promoted'
        active_memories = storage.list_production_memories(active_only=True, db_path=self.test_db)
        self.assertEqual(len(active_memories), 1)
        self.assertEqual(active_memories[0]["pattern"], candidate.pattern)

        updated_cands = storage.list_quarantine_candidates(db_path=self.test_db)
        self.assertEqual(updated_cands[0]["status"], "promoted")

        # 5. Reject candidate test
        cand2 = storage.add_quarantine_candidate(
            pattern="Spam rule",
            proposed_action="Spam action",
            db_path=self.test_db
        )
        rejected = storage.reject_candidate(cand2.candidate_id, notes="False positive", db_path=self.test_db)
        self.assertTrue(rejected)

        rejected_cands = storage.list_quarantine_candidates(status="rejected", db_path=self.test_db)
        self.assertEqual(len(rejected_cands), 1)

if __name__ == "__main__":
    unittest.main()
