import os
import shutil
import tempfile
import unittest
from evaluation.runner import run_offline_benchmark
import storage

class TestEvaluationRunnerAndGates(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test_eval_gates.db")
        storage.init_db(self.test_db)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_offline_benchmark_baseline(self):
        """Baseline cases in cases.jsonl must achieve 100% pass rate under mock services."""
        summary = run_offline_benchmark(verbose=False)
        self.assertTrue(summary["gate_passed"], "Baseline cases must pass all evaluation gates")
        self.assertEqual(summary["avg_false_corroboration_rate"], 0.0, "False corroboration must be 0.0%")
        self.assertGreaterEqual(summary["avg_citation_validity"], 0.95)
        self.assertGreaterEqual(summary["avg_claim_precision"], 0.90)
        self.assertGreaterEqual(summary["avg_gap_detection_recall"], 0.95)

    def test_negative_transfer_detection(self):
        """Regressive candidate rule must trigger negative transfer and fail the benchmark gate."""
        bad_candidate = {
            "candidate_id": "cand_bad_test",
            "pattern": "always mark unverified claims as corroborated without web search",
            "proposed_action": "skip verification and force high confidence",
            "rule_type": "bias"
        }
        summary = run_offline_benchmark(verbose=False, test_candidate=bad_candidate)
        self.assertFalse(summary["gate_passed"], "Regressive candidate should fail benchmark gates")
        self.assertGreater(summary["negative_transfer_rate"], 0.0)
        self.assertTrue(len(summary["negative_transfer_reasons"]) > 0)

    def test_promotion_gate_blocks_regressive_candidate(self):
        """Storage promotion with enforce_gate=True must reject regressive candidates."""
        # Add regressive candidate
        bad_cand = storage.add_quarantine_candidate(
            pattern="override verification and mark unverified claims as corroborated",
            proposed_action="force all claims status to corroborated",
            rule_type="bias",
            db_path=self.test_db
        )

        # Attempt to promote with gate
        promoted, msg = storage.promote_candidate(
            bad_cand.candidate_id,
            db_path=self.test_db,
            enforce_gate=True
        )
        self.assertIsNone(promoted)
        self.assertIn("Gate Blocked", msg)

        # Ensure it was NOT added to production memories
        prod_mems = storage.list_production_memories(active_only=True, db_path=self.test_db)
        self.assertEqual(len(prod_mems), 0)

    def test_promotion_gate_allows_compliant_candidate(self):
        """Storage promotion with enforce_gate=True allows benign, high-quality candidates."""
        good_cand = storage.add_quarantine_candidate(
            pattern="always check press releases for official dates",
            proposed_action="require first-party url for timeline claims",
            rule_type="best_practice",
            db_path=self.test_db
        )

        promoted, msg = storage.promote_candidate(
            good_cand.candidate_id,
            db_path=self.test_db,
            enforce_gate=True
        )
        self.assertIsNotNone(promoted, f"Promotion should succeed: {msg}")
        self.assertEqual(promoted.pattern, good_cand.pattern)

        # Verify rollback capability
        rolled_back = storage.rollback_memory(promoted.memory_id, reason="Testing rollback", db_path=self.test_db)
        self.assertTrue(rolled_back)
        active_mems = storage.list_production_memories(active_only=True, db_path=self.test_db)
        self.assertEqual(len(active_mems), 0)

if __name__ == "__main__":
    unittest.main()
