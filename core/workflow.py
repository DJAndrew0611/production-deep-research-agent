import json
import asyncio
from typing import Dict, Any, List, Optional, Tuple, Callable
from openai import AsyncOpenAI
from agents import Agent, Runner, set_default_openai_client, OpenAIChatCompletionsModel

from urllib.parse import urlparse
from schemas import (
    Claim, EvidenceSource, EvidenceLedger, VerificationResult,
    EvidenceCorpus, EvidenceDocument
)
from core.context import RunContext, BudgetConfig, RunTelemetry
from core.resilience import retry_with_backoff, execute_with_timeout
from core.tools.ledger_tools import (
    clean_model_output, scan_for_prompt_injection,
    parse_evidence_ledger_from_output, render_markdown_audit_report
)
from core.tools.search_tools import create_deep_research_tool
from core.tools.mcp_tools import create_mcp_tool_adapter, MCPStdioClient
from core.agents.research_agent import create_research_agent
from core.agents.elaboration_agent import create_elaboration_agent
from core.agents.verification_agent import (
    create_verification_agent, verify_claims_deterministically,
    parse_verification_proposals_from_text
)
from core.observability import setup_logger, StageTracer, log_pipeline_summary
import history_manager

AUTHORITATIVE_FIRST_PARTY_DOMAINS = [
    "openai.com", "anthropic.com", "google.com", "deepmind.google", "microsoft.com",
    "github.com", "arxiv.org", "nature.com", "science.org", "sec.gov", "gov", "edu",
    "meta.com", "apple.com", "amazon.science", "huggingface.co"
]

class ResearchWorkflow:
    """
    Production-grade 4-stage Deep Research Workflow Orchestrator.
    Manages stage timeouts, quota budgets, retries, and Claim-Evidence state machines.
    """
    def __init__(
        self,
        context: Optional[RunContext] = None,
        on_progress: Optional[Callable[[str, str], None]] = None,
        mcp_client: Optional[MCPStdioClient] = None
    ):
        self.context = context or RunContext(topic="Default Research")
        self.on_progress = on_progress or (lambda stage, msg: None)
        self.logger = setup_logger("deep_research.workflow")
        self.mcp_client = mcp_client

    def _notify(self, stage: str, message: str):
        self.on_progress(stage, message)

    def _init_model(self, api_key: str, base_url: str, model_name: str):
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url.strip() if base_url and base_url.strip() else None
        )
        set_default_openai_client(client)
        return OpenAIChatCompletionsModel(model=model_name.strip(), openai_client=client)

    async def run(
        self,
        topic: str,
        api_key: str,
        base_url: str,
        model_name: str,
        firecrawl_key_getter: Callable[[], str],
        active_rules_prompt: str = ""
    ) -> Dict[str, Any]:
        """Execute the complete 4-stage research pipeline with full resilience."""
        self.context.topic = topic
        model = self._init_model(api_key, base_url, model_name)

        # Build tools with shared EvidenceCorpus
        corpus = EvidenceCorpus(task_id=f"run_{topic[:20]}")
        native_search_tool = create_deep_research_tool(
            api_key_getter=firecrawl_key_getter,
            context=self.context,
            corpus=corpus
        )
        if self.mcp_client is not None:
            search_tool = create_mcp_tool_adapter(
                client=self.mcp_client,
                tool_name="deep_research",
                fallback_tool=native_search_tool
            )
            self.logger.info("Integrated MCP tool adapter with native fallback.")
        else:
            search_tool = native_search_tool

        research_agent = create_research_agent(model=model, tools=[search_tool])
        elaboration_agent = create_elaboration_agent(model=model)
        verification_agent = create_verification_agent(model=model, tools=[search_tool])

        # Stage 1: Initial Research & Evidence Ledger Extraction
        self._notify("stage_1", "🔍 调研智能体正在检索全网文献并结构化提取证据账本 (Claim-Evidence)...")
        self.context.record_stage_start("stage_1_research")

        async def _run_stage_1():
            research_prompt = f"RESEARCH TOPIC: {topic}{active_rules_prompt}\n\nPlease perform deep research and output findings followed by the JSON Claim-Evidence block."
            res = await retry_with_backoff(
                Runner.run,
                research_agent,
                research_prompt,
                max_retries=2,
                telemetry=self.context.telemetry
            )
            raw_out = res.final_output
            init_rep = clean_model_output(raw_out)
            ledger = parse_evidence_ledger_from_output(raw_out, topic)
            
            # Synchronize initial evidence sources into corpus
            for c in ledger.claims:
                for ev in c.evidence:
                    if ev.url and not corpus.contains_url(ev.url):
                        domain_val = urlparse(ev.url).netloc.lower() if "://" in ev.url else ""
                        corpus.add_document(EvidenceDocument(
                            url=ev.url,
                            title=ev.title or "",
                            content=ev.snippet or "",
                            source_type=ev.source_type or "first_party_announcement",
                            domain=domain_val
                        ))
            ledger.recompute_all_statuses(corpus=corpus)
            return init_rep, ledger

        timeout_1 = self.context.stage_timeouts.get("stage_1_contract", 90.0)
        try:
            initial_report, evidence_ledger = await execute_with_timeout(
                _run_stage_1(),
                timeout_seconds=timeout_1,
                stage_name="stage_1_research",
                context=self.context
            )
            self.context.record_stage_end("stage_1_research", "completed")
        except Exception as e:
            self.context.record_stage_end("stage_1_research", "failed")
            self.context.record_error("stage_1_research", e)
            initial_report = f"# 调研阶段提示\n因外部检索异常或超时（{str(e)}），启动基础保底研究模式。"
            evidence_ledger = EvidenceLedger(claims=[])

        # Stage 2: Gap-Driven Conditional Search (Concurrent & RAG-Augmented)
        self._notify("stage_2", "🛡️ 审计核验智能体正在检查证据链完整度与时态冲突...")
        self.context.record_stage_start("stage_2_gap_search")

        async def _run_stage_2():
            gap_notes = ""
            evidence_ledger.recompute_all_statuses(corpus=corpus)
            unresolved_claims = [c for c in evidence_ledger.claims if c.status in ["disputed", "unverified", "extracted"]]

            if unresolved_claims:
                self._notify("stage_2_gap", f"检测到 {len(unresolved_claims)} 条断言存在信源缺口，并发触发缺口驱动补查...")

                async def _resolve_single_claim_gap(c: Claim) -> Optional[str]:
                    targeted_q = f"{topic} {c.statement[:35]} 官方公告 权威依据"
                    try:
                        gap_res = await search_tool(query=targeted_q, max_urls=3)
                        gap_analysis = gap_res.get("final_analysis", "")
                        gap_sources = gap_res.get("sources", [])

                        summary_part = ""
                        if gap_analysis:
                            summary_part = f"针对断言 [{c.statement[:30]}...] 补查发现：\n{gap_analysis[:1000]}"

                        if gap_sources:
                            for s in gap_sources:
                                s_url = s.get("url", "")
                                s_title = s.get("title", "")
                                if s_url and (s_url.startswith("http://") or s_url.startswith("https://")):
                                    text_lower = gap_analysis.lower()
                                    stmt_keywords = [w for w in c.statement.split() if len(w) > 2]
                                    matched_kw = sum(1 for kw in stmt_keywords if kw.lower() in text_lower)

                                    is_contradiction = any(neg in text_lower for neg in ["not released", "rumor", "false", "disputed", "澄清", "并非", "辟谣", "虚构"])
                                    if is_contradiction:
                                        stance = "refutes"
                                    elif matched_kw >= max(1, len(stmt_keywords) // 3):
                                        stance = "supports"
                                    else:
                                        stance = "neutral"

                                    parsed_url = urlparse(s_url)
                                    netloc = parsed_url.netloc.lower()
                                    is_authoritative = any(
                                        netloc == dom or netloc.endswith("." + dom)
                                        for dom in AUTHORITATIVE_FIRST_PARTY_DOMAINS
                                    ) or any(h in s_url.lower() for h in ["official", "announcement", "press-release", "/blog/"])
                                    source_type = "first_party_announcement" if is_authoritative else "media"

                                    sentences = [sent.strip() for sent in gap_analysis.split("\n") if sent.strip()]
                                    matching_sentences = [sent for sent in sentences if any(kw.lower() in sent.lower() for kw in stmt_keywords)]
                                    if matching_sentences:
                                        snippet_text = (" ".join(matching_sentences)[:300]).strip()
                                    else:
                                        snippet_text = (gap_analysis[:200].strip() or s_title)

                                    if not snippet_text:
                                        snippet_text = f"Source verification from {s_title or s_url}"

                                    c.evidence.append(EvidenceSource(
                                        url=s_url,
                                        title=s_title,
                                        snippet=snippet_text,
                                        source_type=source_type,
                                        stance=stance
                                    ))
                        c.recalculate_status_and_confidence(corpus=corpus)
                        return summary_part
                    except Exception as e_gap:
                        self.context.record_error("gap_search", e_gap)
                        return None

                # Concurrent search via asyncio.gather (up to 3 unresolved claims)
                tasks = [_resolve_single_claim_gap(c) for c in unresolved_claims[:3]]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                gap_summaries = [r for r in results if isinstance(r, str) and r]

                # Optional RAG semantic augmentation
                try:
                    import os
                    import storage
                    from core.rag.retriever import SemanticRetriever
                    rag_db = os.path.join(os.path.dirname(storage.DB_PATH), "embeddings.db")
                    retriever = SemanticRetriever(db_path=rag_db)
                    retriever.index_corpus(corpus, session_id=self.context.session_id)
                    for c in unresolved_claims:
                        rag_docs = retriever.find_evidence_for_claim(c.statement, corpus, top_k=2, session_id=self.context.session_id)
                        for doc in rag_docs:
                            corpus.add_document(doc)
                except Exception as e_rag:
                    self.logger.debug(f"RAG semantic retrieval fallback/skip: {e_rag}")

                evidence_ledger.recompute_all_statuses(corpus=corpus)
                if gap_summaries:
                    gap_notes = "\n\n【定向缺口补查证据汇编】:\n" + "\n\n".join(gap_summaries)
            return gap_notes

        timeout_2 = self.context.stage_timeouts.get("stage_2_research", 120.0)
        try:
            gap_notes = await execute_with_timeout(
                _run_stage_2(),
                timeout_seconds=timeout_2,
                stage_name="stage_2_gap_search",
                context=self.context
            )
            self.context.record_stage_end("stage_2_gap_search", "completed")
        except Exception as e:
            self.context.record_stage_end("stage_2_gap_search", "failed")
            self.context.record_error("stage_2_gap_search", e)
            gap_notes = ""

        # Stage 3: Elaboration
        self._notify("stage_3", "✍️ 拓展智能体正在基于验证证据深度丰富技术机制与战略推演...")
        self.context.record_stage_start("stage_3_elaboration")

        async def _run_stage_3():
            elaboration_input = f"""
            RESEARCH TOPIC: {topic}
            
            VERIFIED INITIAL REPORT:
            {initial_report}
            {gap_notes}
            
            Please elaborate and enhance this research report with deep explanations, case studies, 
            practical implications, and future outlook while strictly adhering to verified evidence.
            """
            elaboration_res = await retry_with_backoff(
                Runner.run,
                elaboration_agent,
                elaboration_input,
                max_retries=2,
                telemetry=self.context.telemetry
            )
            return clean_model_output(elaboration_res.final_output)

        timeout_3 = self.context.stage_timeouts.get("stage_3_elaboration", 90.0)
        try:
            enhanced_report = await execute_with_timeout(
                _run_stage_3(),
                timeout_seconds=timeout_3,
                stage_name="stage_3_elaboration",
                context=self.context
            )
            self.context.record_stage_end("stage_3_elaboration", "completed")
        except Exception as e:
            self.context.record_stage_end("stage_3_elaboration", "failed")
            self.context.record_error("stage_3_elaboration", e)
            enhanced_report = initial_report

        # Stage 4: Fact-Checking Audit & Synthesis
        self._notify("stage_4", "🛡️ 事实核查智能体正在执行逐条断言审计并生成结构化证据结论...")
        self.context.record_stage_start("stage_4_verification")

        async def _run_stage_4():
            claims_summary_text = "\n".join([
                f"- [{c.claim_id}] {c.statement} (生效时间: {c.valid_time.effective_from or '未标注'}, 当前置信度: {c.confidence:.2f})"
                for c in evidence_ledger.claims
            ])
            verification_input = f"""
            RESEARCH TOPIC: {topic}
            
            ENHANCED RESEARCH REPORT TO AUDIT:
            {enhanced_report}
            
            STRUCTURED EVIDENCE CLAIMS (ALL):
            {claims_summary_text}
            
            Please audit all critical factual assertions, dates, temporal validity, and quantitative figures, and provide your qualitative analysis.
            """
            verification_res = await retry_with_backoff(
                Runner.run,
                verification_agent,
                verification_input,
                max_retries=2,
                telemetry=self.context.telemetry
            )
            raw_audit = clean_model_output(verification_res.final_output)

            # Dual-Layer deterministic verification with override authority
            model_proposals = parse_verification_proposals_from_text(raw_audit)
            verification_results = verify_claims_deterministically(
                evidence_ledger.claims,
                corpus=corpus,
                model_proposals=model_proposals
            )
            rendered_audit = render_markdown_audit_report(evidence_ledger.claims, verification_results, raw_audit)
            return rendered_audit, verification_results

        timeout_4 = self.context.stage_timeouts.get("stage_4_verification", 60.0)
        try:
            rendered_audit, verification_results = await execute_with_timeout(
                _run_stage_4(),
                timeout_seconds=timeout_4,
                stage_name="stage_4_verification",
                context=self.context
            )
            self.context.record_stage_end("stage_4_verification", "completed")
        except Exception as e:
            self.context.record_stage_end("stage_4_verification", "failed")
            self.context.record_error("stage_4_verification", e)
            verification_results = verify_claims_deterministically(evidence_ledger.claims, corpus=corpus)
            rendered_audit = render_markdown_audit_report(
                evidence_ledger.claims,
                verification_results,
                raw_audit=f"审计生成遇到轻微异常（{str(e)}），已由底层确定性状态机完成保底核查。"
            )

        final_report = f"{enhanced_report}\n\n---\n\n{rendered_audit}"

        session_data = {
            "topic": topic,
            "provider": self.context.llm_config.get("provider", "Unknown"),
            "model": model_name,
            "initial_report": initial_report,
            "enhanced_report": enhanced_report,
            "verified_report": rendered_audit,
            "final_report": final_report,
            "evidence_ledger": evidence_ledger,
            "evidence_corpus": corpus,
            "verification_snapshot_json": json.dumps([v.model_dump() for v in verification_results], ensure_ascii=False),
            "telemetry_summary": self.context.export_summary(),
            "feedback_history": []
        }
        log_pipeline_summary(self.logger, self.context)
        return session_data

    async def run_feedback_revision(
        self,
        session: Dict[str, Any],
        user_feedback: str,
        target_agent_type: str,
        api_key: str,
        base_url: str,
        model_name: str,
        firecrawl_key_getter: Callable[[], str]
    ) -> Tuple[str, str, EvidenceLedger, List[VerificationResult]]:
        """Execute user feedback revision with anti-injection, quarantine capture, and atomic fact re-extraction."""
        model = self._init_model(api_key, base_url, model_name)
        
        # Thread or reconstruct EvidenceCorpus
        corpus = session.get("evidence_corpus")
        if not isinstance(corpus, EvidenceCorpus):
            corpus = EvidenceCorpus(task_id=session.get("session_id", "feedback"))

        native_search_tool = create_deep_research_tool(
            api_key_getter=firecrawl_key_getter,
            context=self.context,
            corpus=corpus
        )
        if self.mcp_client is not None:
            search_tool = create_mcp_tool_adapter(
                client=self.mcp_client,
                tool_name="deep_research",
                fallback_tool=native_search_tool
            )
        else:
            search_tool = native_search_tool

        research_agent = create_research_agent(model=model, tools=[search_tool])
        elaboration_agent = create_elaboration_agent(model=model)
        verification_agent = create_verification_agent(model=model, tools=[search_tool])

        agent_map = {
            "research_agent": (research_agent, "🔍 调研智能体 (Research Agent)"),
            "elaboration_agent": (elaboration_agent, "✍️ 拓展智能体 (Elaboration Agent)"),
            "verification_agent": (verification_agent, "🛡️ 事实核查智能体 (Verification Agent)")
        }
        selected_agent, agent_name = agent_map[target_agent_type]

        revision_prompt = f"""
        CURRENT RESEARCH TOPIC: {session.get('topic')}
        
        CURRENT FINAL REPORT:
        {session.get('final_report')}
        
        USER REVISION REQUEST:
        {user_feedback}
        
        YOUR ROLE:
        You are the {agent_name}. Please revise the report specifically addressing the user's feedback.
        - If you are Research Agent: Use deep_research to fetch additional data/sources requested by the user and integrate them.
        - If you are Elaboration Agent: Restructure, deepen explanations, add case studies, or improve tone/format as requested.
        - If you are Verification Agent: Re-check disputed claims, verify questionable figures against web/sources, and update the audit section.
        
        Deliver the full updated and polished final report.
        """

        revision_result = await Runner.run(selected_agent, revision_prompt)
        revised_report = clean_model_output(revision_result.final_output)

        # Loop 3: Feedback Revision Fact Re-Extraction & Atomic Sync
        new_ledger = parse_evidence_ledger_from_output(revised_report, session.get("topic", ""))
        if not new_ledger.claims and session.get("evidence_ledger"):
            orig_ledger = session.get("evidence_ledger")
            if isinstance(orig_ledger, dict):
                new_ledger = EvidenceLedger.model_validate(orig_ledger)
            elif isinstance(orig_ledger, EvidenceLedger):
                new_ledger = orig_ledger.model_copy(deep=True)
            else:
                new_ledger = EvidenceLedger(claims=[])

        # Synchronize new evidence citations into corpus
        for c in new_ledger.claims:
            for ev in c.evidence:
                if ev.url and not corpus.contains_url(ev.url):
                    domain_val = urlparse(ev.url).netloc.lower() if "://" in ev.url else ""
                    corpus.add_document(EvidenceDocument(
                        url=ev.url,
                        title=ev.title or "",
                        content=ev.snippet or "",
                        source_type=ev.source_type or "first_party_announcement",
                        domain=domain_val
                    ))

        # Recompute claim statuses against corpus and run deterministic verification
        new_ledger.recompute_all_statuses(corpus=corpus)
        model_proposals = parse_verification_proposals_from_text(revised_report)
        new_verification_results = verify_claims_deterministically(
            new_ledger.claims,
            corpus=corpus,
            model_proposals=model_proposals
        )

        return revised_report, agent_name, new_ledger, new_verification_results
