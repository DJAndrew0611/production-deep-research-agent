import json
import os
from typing import Dict, Any, List, Optional

CASES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cases.jsonl')

def load_benchmark_cases() -> List[Dict[str, Any]]:
    """Load evaluation cases from cases.jsonl."""
    cases = []
    if os.path.exists(CASES_FILE):
        with open(CASES_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    cases.append(json.loads(line))
    return cases

class MockSearchService:
    """Deterministic offline search provider matching queries against benchmark cases."""
    def __init__(self, cases: List[Dict[str, Any]]):
        self.cases = {c["case_id"]: c for c in cases}
        self.query_to_case = {}
        for c in cases:
            self.query_to_case[c["topic"].lower()] = c

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        import re
        q_lower = query.lower()
        matched_case = None
        for k, c in self.query_to_case.items():
            keywords = [w for w in re.findall(r'[a-zA-Z0-9_\u4e00-\u9fa5]{2,}', k) if not w.isdigit()]
            if any(kw in q_lower for kw in keywords):
                matched_case = c
                break

        if not matched_case:
            return {
                "success": True,
                "sources": [],
                "final_analysis": "",
                "no_match": True
            }

        docs = matched_case.get("mock_documents", []) if matched_case else []
        sources = [{"url": d["url"], "title": d["title"]} for d in docs]
        formatted_texts = [
            f"### [{d['title']}]\nURL: {d['url']}\n{d['content']}"
            for d in docs
        ]
        
        return {
            "success": True,
            "sources": sources,
            "final_analysis": "\n\n---\n\n".join(formatted_texts)
        }

class MockLLMExtractor:
    """Generates deterministic Agent output simulating the real research pipeline."""
    @staticmethod
    def generate_research_output(case: Dict[str, Any], candidate_rule: Optional[Dict[str, Any]] = None) -> str:
        gt = case.get("ground_truth", {})
        topic = case["topic"]
        trap = case.get("injected_trap", "")
        docs = case.get("mock_documents", [])

        # Check if an injected candidate rule degrades verification standards
        is_adversarial_rule = False
        if candidate_rule:
            pat = candidate_rule.get("pattern", "").lower()
            act = candidate_rule.get("proposed_action", "").lower()
            adversarial_tokens = [
                "skip verification", "override verification", "force corroborated",
                "ignore evidence", "ignore source", "mark unverified as corroborated",
                "bypass check", "ignore all", "bypass safety"
            ]
            is_adversarial_rule = any(tok in pat or tok in act for tok in adversarial_tokens)

        fault = case.get("fault_mode", "general")

        claims_json_list = []
        for idx, doc in enumerate(docs, 1):
            claims_json_list.append({
                "statement": f"{topic}的核心事实经过官方核实：{doc['content'][:80]}。",
                "claim_type": "timeline" if "时间" in topic else "tech_parameter",
                "scope": {"product": topic.split()[0], "release_stage": "GA"},
                "valid_time": {"effective_from": gt.get("effective_from")},
                "status": "corroborated",
                "evidence": [
                    {
                        "url": doc["url"],
                        "title": doc["title"],
                        "snippet": doc["content"][:120],
                        "source_type": doc.get("source_type", "first_party_announcement"),
                        "stance": "supports"
                    }
                ],
                "confidence": 0.95
            })

        if is_adversarial_rule:
            # Adversarial candidate rule forces false corroboration without valid evidence
            if fault == "fake_url":
                fake_ev = [{
                    "url": "https://fabricated-benchmark-leak.ai/swe-99",
                    "title": "Fabricated Leak",
                    "snippet": "Claude 3.5 Sonnet achieves 99% on SWE-bench according to leaked screenshots.",
                    "source_type": "media",
                    "stance": "supports"
                }]
            elif fault == "semantic_mismatch":
                fake_ev = [{
                    "url": docs[0]["url"] if docs else "https://example.com/willow",
                    "title": "Quantum News",
                    "snippet": "The weather across the country was sunny with light breeze today.",
                    "source_type": "media",
                    "stance": "supports"
                }]
            else:
                fake_ev = []

            claims_json_list.append({
                "statement": f"关于【{trap}】的传闻已强制采信。",
                "claim_type": "timeline" if "时间" in topic else "tech_parameter",
                "scope": {"product": topic.split()[0]},
                "valid_time": {"effective_from": "2026-01-01"},
                "status": "corroborated",
                "evidence": fake_ev,
                "confidence": 0.95
            })
            trap_line = f"- 针对外界传闻（{trap}），已强制采信并标记为确认（受注入规则影响）。"
        else:
            # Baseline / safe branch: correctly identifies gaps, disputes, and unverified traps
            if fault == "fake_url":
                trap_status = "unverified"
                trap_ev = [{
                    "url": "https://fabricated-benchmark-leak.ai/swe-99",
                    "title": "Unverified Leak",
                    "snippet": "Leaked claim of 99% benchmark score without first-party verification.",
                    "source_type": "media",
                    "stance": "neutral"
                }]
                trap_desc = f"关于【{trap}】缺乏官方收录源，严格标记为待核验。"
            elif fault == "semantic_mismatch":
                trap_status = "disputed"
                trap_ev = []
                trap_desc = f"关于【{trap}】的引用内容与断言存在实质性语义脱节。"
            elif fault == "injected_snippet":
                trap_status = "disputed"
                trap_ev = []
                trap_desc = f"检测到包含提示词注入特征的恶意攻击片段（{trap}），已安全拦截并标为争议。"
            else:
                trap_status = "disputed"
                trap_ev = []
                trap_desc = f"关于【{trap}】的传闻缺乏一手源佐证，存在信源或时态冲突。"

            claims_json_list.append({
                "statement": trap_desc,
                "claim_type": "timeline" if "时间" in topic else "tech_parameter",
                "scope": {"product": topic.split()[0]},
                "valid_time": {"effective_from": "2026-01-01"},
                "status": trap_status,
                "evidence": trap_ev,
                "confidence": 0.35
            })
            trap_line = f"- 针对外界传闻（{trap}），经比对官方首发时间线与多源信源，判定为存疑失实。"

        # Handle malformed_json fault mode (testing markdown fallback extraction)
        if fault == "malformed_json" and not is_adversarial_rule:
            return f"""# 研究简报：{topic}
根据官方一手材料核验：
- 核心事实：{topic}已于 {gt.get('effective_from')} 由官方团队正式公布，特性包括：{', '.join(gt.get('must_include', []))}。
- 传闻核实：关于【{trap}】的传闻存在事实争议，暂无证据，严格待核验。
"""

        json_str = json.dumps(claims_json_list, ensure_ascii=False, indent=2)

        report_text = f"""# 研究简报：{topic}
根据官方一手材料核验：
- 核心事实生效日期为 {gt.get('effective_from')}。
- 关键特性：{', '.join(gt.get('must_include', []))}。
{trap_line}

```json
{json_str}
```
"""
        return report_text
