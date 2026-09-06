import unittest
from pydantic import ValidationError
from schemas import (
    EvidenceSource, Claim, EvidenceDocument, EvidenceCorpus,
    validate_citation, CitationValidationResult
)

class TestCitationValidator(unittest.TestCase):
    def setUp(self):
        self.doc1 = EvidenceDocument(
            document_id="doc_001",
            url="https://openai.com/index/gpt-5-preview",
            title="GPT-5 Preview Announcement",
            content="OpenAI announced the research preview of GPT-5, but General Availability (GA) is not live yet.",
            source_type="first_party_announcement"
        )
        self.corpus = EvidenceCorpus(
            task_id="task_test_01",
            documents=[self.doc1],
            retrieval_queries=["GPT-5 official launch status"]
        )

    def test_extra_field_forbid_on_evidence_source(self):
        """extra='forbid' must reject unexpected fields."""
        with self.assertRaises(ValidationError):
            EvidenceSource(
                url="https://openai.com/test",
                snippet="Valid snippet",
                unexpected_bogus_field="should_raise"
            )

    def test_empty_snippet_is_invalid_citation(self):
        """Citation with missing or empty snippet must fail validation."""
        ev = EvidenceSource(
            url="https://openai.com/index/gpt-5-preview",
            snippet="",  # Empty snippet
            stance="supports"
        )
        claim = Claim(statement="GPT-5 preview announced", evidence=[ev])
        result = validate_citation(ev, corpus=self.corpus, claim=claim)
        self.assertFalse(result.snippet_present)
        self.assertFalse(result.valid)
        self.assertIn("Empty or missing snippet", result.reasons)

    def test_citation_not_in_corpus_is_invalid(self):
        """Citation pointing to a URL absent from current EvidenceCorpus must be rejected."""
        ev = EvidenceSource(
            url="https://fabricated-external-blog.com/post",
            snippet="Fabricated announcement text",
            stance="supports"
        )
        claim = Claim(statement="GPT-5 preview announced", evidence=[ev])
        result = validate_citation(ev, corpus=self.corpus, claim=claim)
        self.assertTrue(result.snippet_present)
        self.assertFalse(result.exists_in_corpus)
        self.assertFalse(result.valid)
        self.assertIn("URL not present in retrieval corpus", result.reasons)

    def test_semantic_mismatch_fails_citation(self):
        """Citation with snippet completely irrelevant to claim must fail semantic check."""
        ev = EvidenceSource(
            url="https://openai.com/index/gpt-5-preview",
            snippet="The weather in San Francisco today is sunny and mild with light winds.",
            stance="supports"
        )
        claim = Claim(statement="Quantum computing superconductor breakthrough at 300 Kelvin", evidence=[ev])
        result = validate_citation(ev, corpus=self.corpus, claim=claim)
        self.assertEqual(result.semantic_support, "unclear")
        self.assertFalse(result.valid)

    def test_valid_supporting_citation_succeeds(self):
        """Valid citation present in corpus with matching snippet passes validation."""
        ev = EvidenceSource(
            url="https://openai.com/index/gpt-5-preview",
            snippet="OpenAI announced the research preview of GPT-5, but GA is not live yet.",
            stance="supports"
        )
        claim = Claim(statement="GPT-5 research preview announced by OpenAI", evidence=[ev])
        result = validate_citation(ev, corpus=self.corpus, claim=claim)
        self.assertTrue(result.url_valid)
        self.assertTrue(result.exists_in_corpus)
        self.assertTrue(result.snippet_present)
        self.assertEqual(result.semantic_support, "supports")
        self.assertTrue(result.valid)

    def test_claim_recalculate_with_invalid_citation_remains_unverified(self):
        """A claim with invalid citations cannot be corroborated."""
        ev = EvidenceSource(
            url="https://fabricated-news.com/fake",
            snippet="Fake claim support",
            stance="supports"
        )
        claim = Claim(statement="Some unverified statement", evidence=[ev])
        # Recalculate against corpus where fabricated URL does not exist
        status, conf, expl = claim.recalculate_status_and_confidence(corpus=self.corpus)
        self.assertEqual(status, "unverified")
        self.assertLessEqual(conf, 0.40)
        self.assertIn("unverified", status)

if __name__ == "__main__":
    unittest.main()
