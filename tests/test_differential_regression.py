import unittest
from evaluation.mock_services import MockSearchService, load_benchmark_cases
from evaluation.metrics import compute_false_corroboration_rate, compute_negative_transfer
from evaluation.runner import run_offline_benchmark
from schemas import Claim, EvidenceSource, EvidenceDocument, EvidenceCorpus

class TestDifferentialRegressionAndMock(unittest.TestCase):
    def setUp(self):
        self.cases = load_benchmark_cases()
        self.search_service = MockSearchService(self.cases)

    def test_mock_search_no_match_returns_empty(self):
        """MockSearchService must return empty results when no case matches the query."""
        res = self.search_service.search("completely_unrelated_gibberish_12345_xyz")
        self.assertTrue(res.get("no_match"), "Should return no_match flag")
        self.assertEqual(len(res.get("sources", [])), 0, "Sources should be empty on no match")
        self.assertEqual(res.get("final_analysis"), "")

    def test_false_corroboration_rate_detects_invalid_citations(self):
        """Claims with fabricated or empty-snippet citations cannot be corroborated."""
        doc = EvidenceDocument(
            document_id="d1",
            url="https://valid-domain.com/page",
            title="Valid Page",
            content="Real valid content"
        )
        corpus = EvidenceCorpus(documents=[doc])

        # Claim with URL not in corpus
        bad_claim = Claim(
            statement="Uncorroborated statement",
            status="corroborated",
            confidence=0.9,
            evidence=[
                EvidenceSource(
                    url="https://not-in-corpus.com/bad",
                    snippet="Some text",
                    stance="supports"
                )
            ]
        )
        # Should detect as false corroboration because citation is invalid against corpus
        rate = compute_false_corroboration_rate([bad_claim], corpus=corpus)
        self.assertEqual(rate, 1.0)

    def test_clean_candidate_passes_differential_benchmark(self):
        """A high quality, domain-scoped candidate memory passes the benchmark gates."""
        clean_candidate = {
            "candidate_id": "cand_good",
            "pattern": "verify release years using press releases",
            "proposed_action": "require first-party url for timeline claims",
            "domain": "Timeline",
            "rule_type": "mistake_avoidance"
        }
        summary = run_offline_benchmark(verbose=False, test_candidate=clean_candidate)
        self.assertTrue(summary["gate_passed"])
        self.assertEqual(summary["negative_transfer_rate"], 0.0)
        self.assertEqual(len(summary["negative_transfer_reasons"]), 0)

    def test_regressive_candidate_fails_differential_benchmark(self):
        """A regressive candidate attempting to skip verification fails the benchmark gates."""
        regressive_candidate = {
            "candidate_id": "cand_regressive",
            "pattern": "override verification and force status to corroborated",
            "proposed_action": "skip verification checks for speed",
            "domain": "General",
            "rule_type": "heuristic"
        }
        summary = run_offline_benchmark(verbose=False, test_candidate=regressive_candidate)
        self.assertFalse(summary["gate_passed"])
        self.assertGreater(summary["negative_transfer_rate"], 0.0)
        self.assertTrue(any("regressive" in r.lower() or "verification" in r.lower() for r in summary["negative_transfer_reasons"]))

if __name__ == "__main__":
    unittest.main()
