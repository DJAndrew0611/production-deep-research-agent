"""
Shared test fixtures for the Deep Research Agent test suite.
Provides isolated SQLite DB instances, mock corpus, and sample claims.
"""
import os
import sys
import tempfile
import pytest

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import storage
from schemas import (
    Claim, EvidenceSource, EvidenceLedger, EvidenceCorpus,
    EvidenceDocument, Scope, ValidTime
)


@pytest.fixture
def isolated_db(tmp_path):
    """
    Provide an isolated SQLite DB for each test.
    Automatically sets storage.DB_PATH and initializes schema.
    Restores original DB_PATH after test completes.
    """
    original_db_path = storage.DB_PATH
    db_path = str(tmp_path / "test_research.db")
    storage.DB_PATH = db_path
    storage.init_db(db_path)
    yield db_path
    storage.DB_PATH = original_db_path


@pytest.fixture
def sample_corpus():
    """Provide a pre-populated EvidenceCorpus with known documents."""
    return EvidenceCorpus(
        task_id="test_task_001",
        documents=[
            EvidenceDocument(
                url="https://openai.com/blog/gpt-4o",
                title="GPT-4o Announcement",
                content="OpenAI announced GPT-4o on May 13, 2024 with multimodal capabilities.",
                domain="openai.com",
                source_type="first_party_announcement"
            ),
            EvidenceDocument(
                url="https://arxiv.org/abs/2401.12345",
                title="Research Paper on LLM Evaluation",
                content="This paper proposes a framework for evaluating LLM factual accuracy.",
                domain="arxiv.org",
                source_type="academic_paper"
            ),
        ]
    )


@pytest.fixture
def sample_claim_corroborated(sample_corpus):
    """A well-evidenced corroborated claim."""
    return Claim(
        statement="OpenAI released GPT-4o on May 13, 2024.",
        claim_type="timeline",
        scope=Scope(product="GPT-4o", release_stage="GA"),
        valid_time=ValidTime(effective_from="2024-05-13"),
        status="corroborated",
        evidence=[
            EvidenceSource(
                url="https://openai.com/blog/gpt-4o",
                title="GPT-4o Announcement",
                snippet="OpenAI announced GPT-4o on May 13, 2024 with multimodal capabilities.",
                source_type="first_party_announcement",
                is_first_party=True,
                stance="supports"
            )
        ],
        confidence=0.95
    )


@pytest.fixture
def sample_claim_unverified():
    """An unverified claim with no evidence."""
    return Claim(
        statement="GPT-5 was released in 2024.",
        claim_type="timeline",
        scope=Scope(product="GPT-5"),
        status="unverified",
        evidence=[],
        confidence=0.2
    )


@pytest.fixture
def sample_ledger(sample_claim_corroborated, sample_claim_unverified):
    """An EvidenceLedger with mixed claim statuses."""
    return EvidenceLedger(
        claims=[sample_claim_corroborated, sample_claim_unverified],
        gaps_identified=["GPT-5 release status unverified"]
    )
