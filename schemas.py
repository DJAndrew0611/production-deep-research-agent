import uuid
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Literal, Tuple
from pydantic import BaseModel, Field, field_validator, ConfigDict

class Scope(BaseModel):
    """Scope and boundary conditions for a claim."""
    product: Optional[str] = None
    release_stage: Optional[str] = None  # e.g., "Preview", "Beta", "General Availability (GA)"
    target_domain: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)

class ValidTime(BaseModel):
    """Temporal validity boundary for a claim."""
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None

class EvidenceSource(BaseModel):
    """Fine-grained verifiable evidence item supporting or refuting a claim."""
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:8]}")
    url: str
    canonical_url: Optional[str] = None
    title: Optional[str] = None
    source_type: Literal[
        "first_party_announcement", "technical_docs", "media_report",
        "academic_paper", "media", "unknown"
    ] = "media"
    publisher: Optional[str] = None
    domain: Optional[str] = None
    is_first_party: bool = False
    published_at: Optional[str] = None
    retrieved_at: Optional[str] = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    snippet: str = Field(default="")
    quote_locator: Optional[str] = None
    content_hash: Optional[str] = None
    stance: Literal["supports", "refutes", "neutral", "unknown"] = "supports"
    independence_cluster: Optional[str] = None
    injection_flags: List[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("Evidence URL cannot be empty")
        # Strictly reject fake URL placeholders
        forbidden_hosts = ["web-search-context.verified", "example.com/placeholder", "placeholder.com"]
        for fh in forbidden_hosts:
            if fh in v_clean:
                raise ValueError(f"Fake or placeholder URL '{v_clean}' is strictly prohibited.")
        if not (v_clean.startswith("http://") or v_clean.startswith("https://")):
            raise ValueError(f"Evidence URL must be an HTTP/HTTPS URL, got: {v_clean}")
        return v_clean

class EvidenceDocument(BaseModel):
    """Raw crawled document from search/fetch tooling."""
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(default_factory=lambda: f"doc_{uuid.uuid4().hex[:8]}")
    url: str
    canonical_url: Optional[str] = None
    title: str = ""
    content: str = ""
    domain: Optional[str] = None
    source_type: str = "media"
    publisher: Optional[str] = None
    published_at: Optional[str] = None
    retrieved_at: Optional[str] = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    content_hash: Optional[str] = None
    injection_flags: List[str] = Field(default_factory=list)

class EvidenceCorpus(BaseModel):
    """Corpus of all retrieval documents accumulated for a research session."""
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    documents: List[EvidenceDocument] = Field(default_factory=list)
    retrieval_queries: List[str] = Field(default_factory=list)

    def find_document(self, url: str) -> Optional[EvidenceDocument]:
        clean_target = url.strip().rstrip("/").lower()
        for doc in self.documents:
            if doc.url.strip().rstrip("/").lower() == clean_target:
                return doc
            if doc.canonical_url and doc.canonical_url.strip().rstrip("/").lower() == clean_target:
                return doc
        return None

    def contains_url(self, url: str) -> bool:
        return self.find_document(url) is not None

    def add_document(self, doc: EvidenceDocument):
        """Add an EvidenceDocument if not already present by URL."""
        if not self.contains_url(doc.url):
            self.documents.append(doc)

    def merge(self, other: "EvidenceCorpus"):
        """Merge another EvidenceCorpus into this corpus."""
        for doc in other.documents:
            self.add_document(doc)
        for q in other.retrieval_queries:
            if q not in self.retrieval_queries:
                self.retrieval_queries.append(q)

class CitationValidationResult(BaseModel):
    """Detailed audit evaluation for an individual citation."""
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    url_valid: bool
    exists_in_corpus: bool
    snippet_present: bool
    semantic_support: Literal["supports", "refutes", "unclear"]
    time_consistent: bool
    source_quality: float
    valid: bool
    reasons: List[str] = Field(default_factory=list)

def validate_citation(
    evidence: EvidenceSource,
    corpus: Optional[EvidenceCorpus] = None,
    claim: Optional['Claim'] = None
) -> CitationValidationResult:
    """
    Pure validation function checking:
    1. URL format validity (http/https and no fake placeholders)
    2. Existence in current retrieval EvidenceCorpus (if corpus provided)
    3. Snippet presence and non-emptiness
    4. Semantic/token overlap and stance consistency
    5. Time consistency between claim and snippet
    6. Prompt injection flags
    """
    reasons = []

    # 1. URL validity
    url_valid = bool(evidence.url and (evidence.url.startswith("http://") or evidence.url.startswith("https://")))
    forbidden_hosts = ["web-search-context.verified", "example.com/placeholder", "placeholder.com"]
    if any(fh in evidence.url for fh in forbidden_hosts):
        url_valid = False
        reasons.append(f"Forbidden placeholder URL: '{evidence.url}'")
    elif not url_valid:
        reasons.append(f"Invalid URL scheme: '{evidence.url}'")

    # 2. Corpus existence
    if corpus is not None:
        exists_in_corpus = corpus.contains_url(evidence.url)
        if not exists_in_corpus:
            reasons.append("URL not present in retrieval corpus")
    else:
        exists_in_corpus = True

    # 3. Snippet presence
    snippet_clean = (evidence.snippet or "").strip()
    snippet_present = bool(snippet_clean and snippet_clean != "[No snippet provided]")
    if not snippet_present:
        reasons.append("Empty or missing snippet")

    # 4. Semantic overlap and stance determination
    semantic_support: Literal["supports", "refutes", "unclear"] = "unclear"
    if claim and snippet_present:
        def extract_tokens(text: str) -> set:
            tokens = set(re.findall(r'\b[a-zA-Z0-9_\u4e00-\u9fa5]{2,}\b', text.lower()))
            stopwords = {
                "the", "and", "is", "was", "for", "with", "this", "that", "from",
                "are", "were", "been", "have", "has", "had", "will", "would",
                "can", "could", "about", "into", "over", "after", "before", "between",
                "under", "above", "also", "most", "more", "some", "such", "than",
                "they", "them", "these", "those", "what", "which", "who", "whom",
                "when", "where", "why", "how", "all", "any", "both", "each"
            }
            return {t for t in tokens if t not in stopwords}

        claim_tokens = extract_tokens(claim.statement)
        snippet_tokens = extract_tokens(snippet_clean)
        overlap = claim_tokens.intersection(snippet_tokens)
        overlap_ratio = len(overlap) / max(len(claim_tokens), 1)

        if evidence.stance == "refutes":
            semantic_support = "refutes"
        elif evidence.stance == "supports":
            direct_denial_patterns = [
                r"\bfalse\s+claim\b", r"\bhoax\b", r"\bdenied\b", r"\bdisproved\b",
                r"不实", r"辟谣", r"否认", r"假消息"
            ]
            has_direct_denial = any(re.search(pat, snippet_clean, re.IGNORECASE) for pat in direct_denial_patterns)
            if has_direct_denial:
                semantic_support = "refutes"
            elif overlap_ratio >= 0.2 or len(overlap) >= 2:
                semantic_support = "supports"
            else:
                semantic_support = "unclear"
                reasons.append(f"Snippet has insufficient semantic overlap with statement (overlap: {len(overlap)} tokens)")
        else:
            semantic_support = "unclear"
    elif not claim and snippet_present:
        if evidence.stance in ["supports", "refutes"]:
            semantic_support = evidence.stance
        else:
            semantic_support = "unclear"

    # 5. Time consistency
    time_consistent = True
    if claim and claim.valid_time and claim.valid_time.effective_from:
        year_claim = re.search(r'\b(19\d\d|20\d\d)\b', claim.valid_time.effective_from)
        if year_claim:
            claim_year = year_claim.group(1)
            years_in_snippet = set(re.findall(r'\b(19\d\d|20\d\d)\b', snippet_clean))
            if years_in_snippet and claim_year not in years_in_snippet and len(years_in_snippet) == 1:
                time_consistent = False
                reasons.append(f"Temporal mismatch: claim year {claim_year} vs snippet year {list(years_in_snippet)[0]}")

    # 6. Injection flags
    if evidence.injection_flags:
        reasons.append(f"Injection flags present on evidence: {evidence.injection_flags}")

    # 7. Overall Validity
    valid = (
        url_valid and
        exists_in_corpus and
        snippet_present and
        (semantic_support in ["supports", "refutes"]) and
        time_consistent and
        not evidence.injection_flags
    )

    source_quality = 1.0 if evidence.is_first_party else 0.7
    if not valid:
        source_quality = 0.0

    return CitationValidationResult(
        evidence_id=evidence.evidence_id,
        url_valid=url_valid,
        exists_in_corpus=exists_in_corpus,
        snippet_present=snippet_present,
        semantic_support=semantic_support,
        time_consistent=time_consistent,
        source_quality=source_quality,
        valid=valid,
        reasons=reasons
    )

class Claim(BaseModel):
    """Structured Claim with temporal validity, scope, evidence links, and verifiable state."""
    claim_id: str = Field(default_factory=lambda: f"claim_{uuid.uuid4().hex[:8]}")
    statement: str = Field(min_length=3)
    claim_type: str = "general_fact"  # "product_general_availability", "financial_metrics", "tech_parameter", "timeline", "general_fact"
    scope: Optional[Scope] = Field(default_factory=Scope)
    valid_time: Optional[ValidTime] = Field(default_factory=ValidTime)
    status: Literal["extracted", "unverified", "disputed", "corroborated"] = "unverified"
    evidence: List[EvidenceSource] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    notes: Optional[str] = None
    verification_method: Optional[str] = None
    verifier_version: Optional[str] = None
    last_verified_at: Optional[str] = None
    status_reason: Optional[str] = None

    def recalculate_status_and_confidence(self, corpus: Optional[EvidenceCorpus] = None) -> Tuple[str, float, str]:
        """
        Deterministic state-machine transition verified against CitationValidator:
        - Evaluates each evidence source through CitationValidator.
        - If 0 valid evidence sources -> unverified (confidence <= 0.4)
        - If any valid evidence refutes the claim -> disputed (confidence <= 0.5)
        - If at least 1 valid supports and 0 refutes -> corroborated (confidence >= 0.75)
        """
        if not self.evidence:
            self.status = "unverified"
            self.confidence = min(self.confidence, 0.4)
            self.status_reason = "No evidence citations found; claim marked as unverified."
            return self.status, self.confidence, self.status_reason

        # Validate each citation
        validation_results = [validate_citation(ev, corpus=corpus, claim=self) for ev in self.evidence]
        valid_evidences = [ev for ev, res in zip(self.evidence, validation_results) if res.valid]

        if not valid_evidences:
            self.status = "unverified"
            self.confidence = min(self.confidence, 0.4)
            invalid_reasons = "; ".join([r for res in validation_results for r in res.reasons if r])
            self.status_reason = f"All {len(self.evidence)} citation(s) failed validation: {invalid_reasons}"
            return self.status, self.confidence, self.status_reason

        has_supports = any(ev.stance == "supports" for ev in valid_evidences)
        has_refutes = any(ev.stance == "refutes" for ev in valid_evidences)

        if has_refutes:
            self.status = "disputed"
            self.confidence = min(self.confidence, 0.5)
            self.status_reason = "Evidence conflict detected: at least one valid source refutes the statement."
        elif has_supports:
            self.status = "corroborated"
            self.confidence = max(0.75, self.confidence)
            self.status_reason = f"Corroborated by {len(valid_evidences)} valid supporting evidence source(s)."
        else:
            self.status = "unverified"
            self.confidence = min(self.confidence, 0.5)
            self.status_reason = "Attached evidence is neutral or unconfirmed; remaining unverified."

        self.last_verified_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.verifier_version = "v2.0"
        return self.status, self.confidence, self.status_reason

class EvidenceLedger(BaseModel):
    """Aggregate Evidence Ledger maintaining verified claims, conflicts, and gaps."""
    claims: List[Claim] = Field(default_factory=list)
    gaps_identified: List[str] = Field(default_factory=list)
    summary: Optional[str] = ""

    def get_corroborated_claims(self) -> List[Claim]:
        return [c for c in self.claims if c.status == "corroborated"]

    def get_disputed_claims(self) -> List[Claim]:
        return [c for c in self.claims if c.status == "disputed"]

    def get_unverified_claims(self) -> List[Claim]:
        return [c for c in self.claims if c.status in ["unverified", "extracted"]]

    def has_critical_gaps(self) -> bool:
        return len(self.gaps_identified) > 0 or any(c.status in ["disputed", "unverified", "extracted"] for c in self.claims)

    def recompute_all_statuses(self, corpus: Optional[EvidenceCorpus] = None):
        """Recompute deterministic statuses for all claims against corpus."""
        self.gaps_identified.clear()
        for c in self.claims:
            st, conf, expl = c.recalculate_status_and_confidence(corpus=corpus)
            if st in ["disputed", "unverified", "extracted"]:
                self.gaps_identified.append(f"[{c.claim_id}] {st.upper()}: {c.statement[:60]} ({expl})")

class QuarantineCandidate(BaseModel):
    """Candidate memory/heuristic awaiting regression evaluation or manual promotion."""
    candidate_id: str = Field(default_factory=lambda: f"cand_{uuid.uuid4().hex[:8]}")
    source_task_id: Optional[str] = None
    rule_type: str = "mistake_avoidance"  # "mistake_avoidance", "search_heuristic", "fact_correction"
    domain: str = "General"
    pattern: str
    proposed_action: str
    status: Literal["quarantined", "promoted", "rejected", "rolled_back"] = "quarantined"
    prompt_injection_scan: str = "passed"  # "passed", "flagged"
    review_notes: Optional[str] = None
    eval_metrics: Optional[Dict[str, Any]] = None
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

class ProductionMemory(BaseModel):
    """Promoted, active production rule/memory governing agent behavior."""
    memory_id: str = Field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:8]}")
    candidate_id: Optional[str] = None
    domain: str = "General"
    rule_type: str = "mistake_avoidance"
    pattern: str
    approved_action: str
    version: str = "v1.0"
    eval_score: Optional[float] = None
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    promoted_at: Optional[str] = None
    rollback_reason: Optional[str] = None
    is_active: bool = True

class VerificationResult(BaseModel):
    """Fine-grained verification outcome for an individual claim."""
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    verdict: Literal["verified", "disputed", "unverified"]
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    supporting_evidence_ids: List[str] = Field(default_factory=list)
    refuting_evidence_ids: List[str] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)
    reasoning_summary: str = ""
    verifier_version: str = "v2.0"
    verified_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

class ReportRevision(BaseModel):
    """Immutable versioned snapshot of a report revision, corresponding ledger and verification audits."""
    model_config = ConfigDict(extra="forbid")

    revision_id: str = Field(default_factory=lambda: f"rev_{uuid.uuid4().hex[:8]}")
    session_id: str
    parent_revision_id: Optional[str] = None
    revision_no: int = 1
    initial_report: Optional[str] = ""
    enhanced_report: Optional[str] = ""
    verified_report: Optional[str] = ""
    final_report: str
    ledger_snapshot_json: str
    verification_snapshot_json: Optional[str] = None
    change_reason: str = "Initial generation"
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
