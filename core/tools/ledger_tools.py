import json
import re
from typing import List, Tuple, Dict, Any, Optional
from schemas import Claim, Scope, ValidTime, EvidenceSource, EvidenceLedger, VerificationResult

def clean_model_output(text: str) -> str:
    """Clean model outputs by removing reasoning scratchpad / think tags."""
    if not text:
        return ""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    for marker in ["## 核查方法说明", "## 🛡️", "# "]:
        pos = text.find(marker)
        if pos != -1 and pos < 2500:
            text = text[pos:]
            break
    return text.strip()

def scan_for_prompt_injection(text: str) -> Tuple[bool, str]:
    """Basic defensive scan against prompt injection and jailbreaks."""
    dangerous_patterns = [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"system\s+prompt\s+override",
        r"bypass\s+safety",
        r"dan\s+mode",
        r"<script.*?>",
        r"javascript:",
        r"eval\("
    ]
    for pattern in dangerous_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True, f"Matched injection pattern: {pattern}"
    return False, "passed"

def classify_feedback_intent(feedback: str) -> str:
    """Classify user revision feedback to pick the most appropriate agent."""
    feedback_lower = feedback.lower()

    # 1. Verification keywords
    verify_kw = [
        "核实", "事实", "准确", "质疑", "数据对不对", "验算", "真伪", "打假", 
        "有误", "假新闻", "真实性", "真实", "真假", "可信", "出处", "verify", 
        "fact", "accuracy", "audit", "true", "false", "check claim", "doubt", "citation"
    ]
    if any(k in feedback_lower for k in verify_kw):
        return "verification_agent"

    # 2. Research keywords
    research_kw = [
        "搜索", "检索", "查一下", "补充数据", "最新进展", "额外信息", "最新消息", 
        "联网查", "找资料", "补充来源", "search", "find", "latest", "more data", 
        "web", "lookup", "sources", "recent"
    ]
    if any(k in feedback_lower for k in research_kw):
        return "research_agent"

    # 3. Default to elaboration for tone, style, explanation, structure, case studies
    return "elaboration_agent"

def parse_evidence_ledger_from_output(output_text: str, default_topic: str) -> EvidenceLedger:
    """Extract structured Claim-Evidence items into an EvidenceLedger object."""
    json_match = re.search(r'```json\s*(.*?)\s*```', output_text, re.DOTALL)
    raw_claims = []

    if json_match:
        try:
            parsed = json.loads(json_match.group(1))
            if isinstance(parsed, list):
                raw_claims = parsed
            elif isinstance(parsed, dict) and "claims" in parsed:
                raw_claims = parsed["claims"]
        except Exception:
            pass

    claims = []
    gaps = []

    if raw_claims:
        for idx, item in enumerate(raw_claims, 1):
            try:
                scope_d = item.get("scope") or {}
                valid_d = item.get("valid_time") or {}
                ev_list = []
                for ev in item.get("evidence", []):
                    stype = ev.get("source_type", "media")
                    is_fp = ev.get("is_first_party", stype in ["first_party_announcement", "first_party_docs", "official"])
                    ev_list.append(EvidenceSource(
                        url=ev.get("url", ""),
                        title=ev.get("title"),
                        snippet=ev.get("snippet", ""),
                        source_type=stype,
                        is_first_party=is_fp,
                        published_at=ev.get("published_at"),
                        stance=ev.get("stance", "supports")
                    ))

                c = Claim(
                    statement=item.get("statement", f"Claim {idx}"),
                    claim_type=item.get("claim_type", "general_fact"),
                    scope=Scope(
                        product=scope_d.get("product"),
                        release_stage=scope_d.get("release_stage"),
                        target_domain=scope_d.get("target_domain")
                    ),
                    valid_time=ValidTime(
                        effective_from=valid_d.get("effective_from"),
                        effective_to=valid_d.get("effective_to")
                    ),
                    status=item.get("status", "corroborated"),
                    evidence=ev_list,
                    confidence=float(item.get("confidence", 0.95))
                )
                claims.append(c)
                if c.status in ["disputed", "unverified"]:
                    gaps.append(f"Dispute in claim [{c.statement[:40]}...]")
            except Exception:
                continue

    # Fallback: if no JSON claims parsed, construct baseline claims from report structure (strictly unverified)
    if not claims:
        lines = [line.strip() for line in output_text.split("\n") if line.strip().startswith("- ") or line.strip().startswith("1. ")]
        for idx, line in enumerate(lines[:5], 1):
            clean_stmt = line.lstrip("- 1234567890. ")
            if len(clean_stmt) > 5:
                claims.append(Claim(
                    statement=clean_stmt,
                    claim_type="general_fact",
                    status="unverified",
                    evidence=[],
                    confidence=0.35,
                    notes="从报告文本提炼的初始论点，暂无外部佐证链接，严格标记为待核验"
                ))

    ledger = EvidenceLedger(claims=claims, gaps_identified=gaps)
    ledger.recompute_all_statuses()
    return ledger

def render_markdown_audit_report(
    claims: List[Claim],
    verification_results: List[VerificationResult],
    raw_audit: str = ""
) -> str:
    """Programmatically render structured Markdown audit report from claims and verification results."""
    num_verified = sum(1 for v in verification_results if v.verdict == "verified")
    num_disputed = sum(1 for v in verification_results if v.verdict == "disputed")
    num_unverified = sum(1 for v in verification_results if v.verdict == "unverified")

    rendered_audit = f"""## 🛡️ 事实核查与可信度审计报告 (Fact-Checking & Credibility Audit v2.0)
- **核查范围说明**: 依据 Claim-Evidence 证据链与 CitationValidator 规则对全量 {len(claims)} 条断言进行逐条审计。
- **审计断言总数**: {len(claims)} 条
- **确证通过 (Verified)**: {num_verified} 条 | **争议冲突 (Disputed)**: {num_disputed} 条 | **待核验 (Unverified)**: {num_unverified} 条

### 逐条断言核验明细
| 断言编号 | 断言陈述 | 状态机判定 | 置信度 | 审计结论与归因 |
| :--- | :--- | :--- | :--- | :--- |
"""
    for c, vr in zip(claims, verification_results):
        v_icon = "✅ 确证通过" if vr.verdict == "verified" else ("⚠️ 存在争议" if vr.verdict == "disputed" else "❓ 待核验")
        stmt_short = c.statement[:40] + ("..." if len(c.statement) > 40 else "")
        rendered_audit += f"| `{c.claim_id}` | {stmt_short} | {v_icon} | {vr.confidence:.2f} | {vr.reasoning_summary} |\n"

    if raw_audit:
        rendered_audit += f"\n\n### 审计专家深度综合分析\n{raw_audit}"

    return rendered_audit
