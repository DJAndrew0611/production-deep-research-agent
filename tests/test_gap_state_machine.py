import unittest
from schemas import Claim, EvidenceSource, EvidenceLedger

class TestGapStateMachine(unittest.TestCase):
    def test_unverified_without_evidence(self):
        """Claim with zero evidence must deterministically evaluate to unverified with confidence <= 0.4."""
        claim = Claim(
            claim_id="c1",
            statement="Model X was released in 2026",
            status="extracted",
            confidence=0.9
        )
        status, conf, explanation = claim.recalculate_status_and_confidence()
        self.assertEqual(status, "unverified")
        self.assertLessEqual(conf, 0.4)
        self.assertIn("No evidence", explanation)

    def test_disputed_on_refuting_evidence(self):
        """Claim with refuting evidence must evaluate to disputed."""
        claim = Claim(
            claim_id="c2",
            statement="Model X was released in 2026",
            evidence=[
                EvidenceSource(
                    url="https://official-blog.com/post",
                    snippet="Model X has not been released yet.",
                    stance="refutes"
                )
            ]
        )
        status, conf, explanation = claim.recalculate_status_and_confidence()
        self.assertEqual(status, "disputed")
        self.assertLessEqual(conf, 0.5)
        self.assertIn("refutes", explanation)

    def test_disputed_on_conflicting_evidence(self):
        """Claim with both supporting and refuting sources must evaluate to disputed."""
        claim = Claim(
            claim_id="c3",
            statement="Model X was released in 2026",
            evidence=[
                EvidenceSource(
                    url="https://news-site.com/report",
                    snippet="Model X released yesterday.",
                    stance="supports"
                ),
                EvidenceSource(
                    url="https://official-site.com/statement",
                    snippet="Rumors of release are false.",
                    stance="refutes"
                )
            ]
        )
        status, conf, explanation = claim.recalculate_status_and_confidence()
        self.assertEqual(status, "disputed")
        self.assertLessEqual(conf, 0.5)

    def test_corroborated_on_supporting_evidence(self):
        """Claim with supporting evidence and no refutations evaluates to corroborated."""
        claim = Claim(
            claim_id="c4",
            statement="Model X was released in Nov 2025",
            evidence=[
                EvidenceSource(
                    url="https://official-company.com/blog/release",
                    snippet="Model X is officially released.",
                    stance="supports"
                )
            ]
        )
        status, conf, explanation = claim.recalculate_status_and_confidence()
        self.assertEqual(status, "corroborated")
        self.assertGreaterEqual(conf, 0.75)
        self.assertIn("Corroborated", explanation)

    def test_ledger_recompute_all_statuses(self):
        """EvidenceLedger recompute_all_statuses updates all contained claims in-place."""
        c_unv = Claim(claim_id="c1", statement="Rumor of product Y", evidence=[])
        c_cor = Claim(
            claim_id="c2",
            statement="Product Z officially available",
            evidence=[
                EvidenceSource(
                    url="https://authoritative.org/facts",
                    snippet="Product Z is now GA.",
                    stance="supports"
                )
            ]
        )
        ledger = EvidenceLedger(claims=[c_unv, c_cor])
        ledger.recompute_all_statuses()

        self.assertEqual(ledger.claims[0].status, "unverified")
        self.assertLessEqual(ledger.claims[0].confidence, 0.4)
        self.assertEqual(ledger.claims[1].status, "corroborated")
        self.assertGreaterEqual(ledger.claims[1].confidence, 0.75)

        # Unverified claim should be tracked in gaps_identified
        self.assertTrue(ledger.has_critical_gaps())
        self.assertTrue(any("c1" in gap for gap in ledger.gaps_identified))

if __name__ == "__main__":
    unittest.main()
