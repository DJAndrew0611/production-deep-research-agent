from typing import List, Dict, Any, Tuple, Optional
from schemas import Claim, EvidenceLedger, EvidenceCorpus, validate_citation

def compute_claim_precision(generated_text: str, ground_truth: Dict[str, Any]) -> float:
    """Evaluate whether generated text respects factual boundaries and avoids forbidden traps."""
    score = 1.0
    text_lower = generated_text.lower()
    
    # Must include
    for inc in ground_truth.get("must_include", []):
        if inc.lower() not in text_lower:
            score -= 0.25

    # Must NOT include
    for exc in ground_truth.get("must_not_include", []):
        if exc.lower() in text_lower:
            score -= 0.50

    return max(0.0, min(1.0, score))

def compute_citation_validity(
    claims: List[Claim],
    retrieved_urls: Optional[List[str]] = None,
    corpus: Optional[EvidenceCorpus] = None
) -> float:
    """
    Check if all evidence citations satisfy CitationValidator:
    - URL format valid and HTTP/HTTPS
    - Snippet present and non-empty
    - Present in corpus or retrieved_urls
    """
    total_evidences = 0
    valid_evidences = 0

    for claim in claims:
        for ev in claim.evidence:
            total_evidences += 1
            res = validate_citation(ev, corpus=corpus, claim=claim)
            if corpus is None and retrieved_urls is not None:
                # Standalone check against url list
                if res.url_valid and res.snippet_present and (ev.url in retrieved_urls):
                    valid_evidences += 1
            elif res.valid:
                valid_evidences += 1

    if total_evidences == 0:
        return 1.0
    return valid_evidences / total_evidences

def compute_false_corroboration_rate(
    claims: List[Claim],
    corpus: Optional[EvidenceCorpus] = None
) -> float:
    """
    Calculate the ratio of claims marked 'corroborated' without valid supporting evidence.
    Uses CitationValidator to ensure citation validity against the retrieval corpus.
    CRITICAL GATE REQUIREMENT: Must be strictly 0.0!
    """
    if not claims:
        return 0.0
    
    false_corroborations = 0
    for claim in claims:
        if claim.status == "corroborated":
            # Check for at least one valid supporting evidence source
            valid_supports = []
            for ev in claim.evidence:
                val_res = validate_citation(ev, corpus=corpus, claim=claim)
                if val_res.valid and val_res.semantic_support == "supports":
                    valid_supports.append(ev)
            
            if not valid_supports or claim.confidence < 0.6:
                false_corroborations += 1

    return false_corroborations / len(claims)

def compute_gap_detection_recall(ledger: EvidenceLedger, case: Dict[str, Any]) -> float:
    """Check if injected traps or gaps were successfully caught."""
    if not case.get("expected_gap_detected"):
        return 1.0
    return 1.0 if ledger.has_critical_gaps() else 0.0

def compute_negative_transfer(
    candidate_rule: Dict[str, Any],
    baseline_results: Optional[List[Dict[str, Any]]] = None,
    candidate_results: Optional[List[Dict[str, Any]]] = None
) -> Tuple[float, List[str]]:
    """
    Evaluate if a newly proposed rule degrades or corrupts performance:
    1. Static heuristic/security check (anti-verification keywords, injection).
    2. Differential regression test comparing Baseline case metrics vs Candidate metrics.
    Returns (negative_transfer_rate, reasons).
    """
    failed_reasons = []
    pattern = candidate_rule.get("pattern", "").lower()
    action = candidate_rule.get("proposed_action", "").lower()

    # 1. Prompt Injection, Anti-Verification & Malicious Token Scan
    regressive_tokens = [
        "ignore all", "system prompt", "dan mode", "bypass safety",
        "delete everything", "eval(", "javascript:",
        "override verification", "skip verification", "mark unverified as corroborated",
        "force corroborated", "ignore evidence", "ignore source", "bypass check"
    ]
    for it in regressive_tokens:
        if it in pattern or it in action:
            failed_reasons.append(f"Regressive/injection pattern detected: '{it}'")

    # 2. Missing domain scope
    if not candidate_rule.get("domain", "").strip():
        failed_reasons.append("Candidate rule lacks domain boundary, risking cross-domain negative transfer.")

    # 3. Empty or overly short pattern
    if len(pattern) < 5 or len(action) < 5:
        failed_reasons.append("Candidate rule pattern/action is overly vague or empty.")

    # 4. Differential Regression Comparison
    if baseline_results and candidate_results:
        for b_case, c_case in zip(baseline_results, candidate_results):
            cid = b_case.get("case_id", "unknown")
            if c_case.get("precision", 0) < b_case.get("precision", 0) - 0.05:
                failed_reasons.append(f"Differential regression on {cid}: precision dropped from {b_case['precision']*100:.1f}% to {c_case['precision']*100:.1f}%")
            if c_case.get("false_corroboration_rate", 0) > b_case.get("false_corroboration_rate", 0):
                failed_reasons.append(f"Differential regression on {cid}: false corroboration increased to {c_case['false_corroboration_rate']*100:.1f}%")
            if c_case.get("citation_validity", 0) < b_case.get("citation_validity", 0) - 0.05:
                failed_reasons.append(f"Differential regression on {cid}: citation validity dropped from {b_case['citation_validity']*100:.1f}% to {c_case['citation_validity']*100:.1f}%")

    negative_transfer_rate = 0.0 if not failed_reasons else 1.0
    return negative_transfer_rate, failed_reasons
