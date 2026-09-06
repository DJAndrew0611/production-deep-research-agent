import json
import re
from typing import List, Optional, Any, Dict
from agents import Agent
from schemas import Claim, VerificationResult, EvidenceCorpus

VERIFICATION_AGENT_INSTRUCTIONS = """You are a rigorous Fact-Checking & Gap-Auditing Agent.
When given an enhanced research report along with the evidence ledger and context:
1. Systematically audit all critical factual assertions, dates, temporal validity, and quantitative figures.
2. Cross-verify assertions against sources. If any crucial claim has conflicting stances, missing first-party evidence,
   or temporal discrepancies, you may call the deep_research tool for targeted verification.
3. Output the structured audit section in Chinese markdown starting with:
   ## 🛡️ 事实核查与可信度审计报告 (Fact-Checking & Credibility Audit)
   - **核查方法说明**: 简述本次核查覆盖的关键断言范围与对照信源。
   - **核查结论总览 (Audit Summary)**: 事实准确度评级 (高 / 中 / 需关注)、核查断言数量。
   - **关键事实核对清单 (Verified Key Claims)**: 表格列出经过双重核验的核心事实、依据与结论。
   - **存疑与修正说明 (Corrections & Caveats)**: 若有任何夸大表述、时间差或需特别补充的前提条件，在此详细指出。
   - **信源权威度评估 (Source Reliability Assessment)**: 对报告引用的数据源与网络来源的权威性进行评级。
   - **结论性建议**: 针对报告正文的修正与阅读建议。

4. At the very end of your audit, output a structured JSON proposal block proposing verdicts for the audited claims:
```json:verification_proposals
[
  {
    "claim_id": "CLM-...",
    "proposed_verdict": "verified" | "disputed" | "unverified",
    "reasoning": "简要核验理由与信源佐证说明"
  }
]
```

CRITICAL REQUIREMENT:
- Do NOT output internal scratchpad monologues or English planning notes.
- Start directly with the formal markdown audit report in Chinese.
- The final JSON block will be processed by the deterministic arbitration engine as a proposal.
"""

def create_verification_agent(model: Any, tools: Optional[List[Any]] = None) -> Agent:
    """Instantiate Fact-Checking & Gap-Auditing Agent."""
    return Agent(
        name="verification_agent",
        model=model,
        instructions=VERIFICATION_AGENT_INSTRUCTIONS,
        tools=tools or []
    )

def parse_verification_proposals_from_text(text: str) -> List[Dict[str, Any]]:
    """
    Extract structured model verification proposals from markdown text.
    Looks for ```json:verification_proposals or general json blocks containing claim_id.
    """
    if not text:
        return []
    
    # 1. Try explicit block tag
    match = re.search(r'```json:verification_proposals\s*([\s\S]*?)\s*```', text, re.IGNORECASE)
    if not match:
        # 2. Try generic json blocks
        for m in re.finditer(r'```(?:json)?\s*(\[\s*\{[\s\S]*?\}\s*\])\s*```', text, re.IGNORECASE):
            try:
                parsed = json.loads(m.group(1))
                if isinstance(parsed, list) and any("claim_id" in item for item in parsed if isinstance(item, dict)):
                    return parsed
            except Exception:
                continue
        return []

    try:
        parsed = json.loads(match.group(1).strip())
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []

def verify_claims_deterministically(
    claims: List[Claim],
    corpus: Optional[EvidenceCorpus] = None,
    model_proposals: Optional[List[Dict[str, Any]]] = None
) -> List[VerificationResult]:
    """
    Dual-Layer Claim Verification:
    - Layer 1: LLM qualitative reasoning and proposal extraction (via model_proposals).
    - Layer 2: Deterministic Python state machine and CitationValidator acting as final arbiter.
    Ensures objective verdicts, prevents LLM hallucinated verification with override authority.
    """
    proposals_by_id: Dict[str, Dict[str, Any]] = {}
    if model_proposals:
        for p in model_proposals:
            if isinstance(p, dict) and "claim_id" in p:
                proposals_by_id[p["claim_id"]] = p

    verification_results = []
    for c in claims:
        st_c, conf_c, expl_c = c.recalculate_status_and_confidence(corpus=corpus)
        rule_verdict = "verified" if st_c == "corroborated" else ("disputed" if st_c == "disputed" else "unverified")
        
        proposal = proposals_by_id.get(c.claim_id)
        if proposal:
            m_verdict = proposal.get("proposed_verdict", "").lower()
            m_reason = proposal.get("reasoning", "")
            
            # Rule Engine Override: Model proposed 'verified' but rule engine found lack of evidence/corpus
            if m_verdict == "verified" and rule_verdict != "verified":
                verdict = rule_verdict
                expl_c = f"{expl_c} | [规则引擎仲裁覆盖: 模型提议 'verified'，但确定性核查未满足准入门槛（状态: {st_c}），强制覆盖为 {rule_verdict}]"
            # Qualitative Disputation: Model identified subtle semantic contradiction that rule didn't catch
            elif m_verdict == "disputed":
                verdict = "disputed"
                conf_c = min(conf_c, 0.49)
                expl_c = f"{expl_c} | [模型语义质询生效: 语义核查提议 'disputed'（理由: {m_reason}），标记为存疑待补查]"
            else:
                verdict = rule_verdict
        else:
            verdict = rule_verdict

        sup_ids = [ev.evidence_id for ev in c.evidence if ev.stance == "supports"]
        ref_ids = [ev.evidence_id for ev in c.evidence if ev.stance == "refutes"]
        missing = ["缺乏一手发布公告支持" if not any(ev.is_first_party for ev in c.evidence) else ""]
        missing = [m for m in missing if m]
        
        vr = VerificationResult(
            claim_id=c.claim_id,
            verdict=verdict,
            confidence=conf_c,
            supporting_evidence_ids=sup_ids,
            refuting_evidence_ids=ref_ids,
            missing_evidence=missing,
            reasoning_summary=expl_c
        )
        verification_results.append(vr)
    return verification_results
