import asyncio
import json
import re
import streamlit as st
from typing import Dict, Any, List, Optional, Tuple
from openai import AsyncOpenAI
from agents import Agent, Runner, trace, set_default_openai_client, OpenAIChatCompletionsModel
from firecrawl import FirecrawlApp
from agents.tool import function_tool

import history_manager
import config_manager
import benchmark_runner
from schemas import (
    Claim, EvidenceSource, EvidenceLedger, Scope, ValidTime,
    QuarantineCandidate, ProductionMemory, ReportRevision, VerificationResult
)
from core.context import RunContext, BudgetConfig, RunTelemetry
from core.tools.ledger_tools import (
    clean_model_output,
    scan_for_prompt_injection,
    classify_feedback_intent,
    parse_evidence_ledger_from_output,
    render_markdown_audit_report
)
from core.tools.search_tools import create_deep_research_tool
from core.agents import (
    create_research_agent,
    create_elaboration_agent,
    create_verification_agent
)
from core.workflow import ResearchWorkflow

# Set page configuration
st.set_page_config(
    page_title="AI Deep Research Agent (Self-Evolving v2.0)",
    page_icon="📘",
    layout="wide"
)

# Preset providers configuration
PROVIDERS = {
    "DeepSeek": {
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
        "key_label": "DeepSeek API Key",
        "key_help": "在 https://platform.deepseek.com 获取 API Key"
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "key_label": "OpenAI API Key",
        "key_help": "在 https://platform.openai.com 获取 API Key"
    },
    "阿里云通义千问 (Qwen)": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "key_label": "DashScope API Key",
        "key_help": "在阿里云 DashScope 控制台获取"
    },
    "硅基流动 (SiliconFlow)": {
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V4-Flash",
        "key_label": "SiliconFlow API Key",
        "key_help": "在 https://cloud.siliconflow.cn 获取"
    },
    "自定义 (Custom OpenAI-Compatible)": {
        "base_url": "",
        "default_model": "",
        "key_label": "Custom LLM API Key",
        "key_help": "任意兼容 OpenAI 规范的 API Key"
    }
}

# Load saved config from config.json
saved_cfg = config_manager.load_config()
saved_provider = saved_cfg.get("last_selected_provider", "DeepSeek")
saved_firecrawl = config_manager.get_firecrawl_key()
saved_custom_base_url = saved_cfg.get("custom_base_url", "")
saved_custom_model_name = saved_cfg.get("custom_model_name", "")

# Initialize session state variables
if "llm_provider" not in st.session_state:
    st.session_state.llm_provider = saved_provider
if "llm_api_key" not in st.session_state:
    st.session_state.llm_api_key = config_manager.get_provider_key(st.session_state.llm_provider)
if "firecrawl_api_key" not in st.session_state:
    st.session_state.firecrawl_api_key = saved_firecrawl
if "active_session" not in st.session_state:
    st.session_state.active_session = None
if "research_topic_val" not in st.session_state:
    st.session_state.research_topic_val = ""
if "last_provider_selected" not in st.session_state:
    st.session_state.last_provider_selected = st.session_state.llm_provider
if "benchmark_results" not in st.session_state:
    st.session_state.benchmark_results = None

# Note: classify_feedback_intent and scan_for_prompt_injection imported from core.tools.ledger_tools

# Sidebar: Configuration, Quarantine Governance & History
with st.sidebar:
    st.title("⚙️ 模型与服务配置")
    
    # Select Provider
    provider_options = list(PROVIDERS.keys())
    default_idx = provider_options.index(st.session_state.llm_provider) if st.session_state.llm_provider in provider_options else 0
    selected_provider = st.selectbox(
        "选择大模型服务商 (LLM Provider)",
        options=provider_options,
        index=default_idx
    )
    
    # If user switched provider, update active key from saved provider keys
    if selected_provider != st.session_state.last_provider_selected:
        st.session_state.last_provider_selected = selected_provider
        st.session_state.llm_provider = selected_provider
        st.session_state.llm_api_key = config_manager.get_provider_key(selected_provider)
        config_manager.save_config({"last_selected_provider": selected_provider})
        st.rerun()

    provider_config = PROVIDERS[selected_provider]
    
    # API Key input
    llm_api_key = st.text_input(
        provider_config["key_label"],
        value=st.session_state.llm_api_key,
        type="password",
        help=provider_config["key_help"]
    )
    if llm_api_key != st.session_state.llm_api_key:
        st.session_state.llm_api_key = llm_api_key
        config_manager.set_provider_key(selected_provider, llm_api_key)
        
    # Base URL & Model Name
    if selected_provider == "自定义 (Custom OpenAI-Compatible)":
        base_url = st.text_input("Base URL", value=saved_custom_base_url, placeholder="https://api.example.com/v1")
        model_name = st.text_input("Model Name", value=saved_custom_model_name, placeholder="e.g. gpt-4o, deepseek-chat")
        if base_url != saved_custom_base_url or model_name != saved_custom_model_name:
            config_manager.save_config({"custom_base_url": base_url, "custom_model_name": model_name})
    else:
        saved_model = saved_cfg.get("saved_models", {}).get(selected_provider, provider_config["default_model"])
        base_url = st.text_input("Base URL", value=provider_config["base_url"])
        model_name = st.text_input("Model Name", value=saved_model)
        if model_name != saved_model:
            saved_models = saved_cfg.get("saved_models", {})
            saved_models[selected_provider] = model_name
            config_manager.save_config({"saved_models": saved_models})
        
    st.divider()
    
    # Firecrawl API Key
    st.subheader("🌐 联网搜索配置")
    firecrawl_api_key = st.text_input(
        "Firecrawl API Key", 
        value=st.session_state.firecrawl_api_key,
        type="password",
        help="在 https://www.firecrawl.dev 获取"
    )
    if firecrawl_api_key != st.session_state.firecrawl_api_key:
        st.session_state.firecrawl_api_key = firecrawl_api_key
        config_manager.set_firecrawl_key(firecrawl_api_key)

    st.caption("🔒 凭据已自动保存在本地配置 (下次启动免输入)")
    with st.expander("⚙️ 凭据管理"):
        if st.button("🗑️ 清空所有已保存的 API Key", use_container_width=True):
            config_manager.clear_all_credentials()
            st.session_state.llm_api_key = ""
            st.session_state.firecrawl_api_key = ""
            st.success("已清除所有本地已保存凭据！")
            st.rerun()

    st.divider()

    # Memory Quarantine & Governance Section (Slow Loop)
    st.subheader("🛡️ 记忆隔离区与治理")
    with st.expander("🔍 经验审查与生产晋级 (Quarantine)", expanded=False):
        pending_candidates = history_manager.list_quarantine_candidates(status="quarantined")
        st.caption(f"隔离待审经验: {len(pending_candidates)} 条")
        
        if pending_candidates:
            for cand in pending_candidates:
                st.markdown(f"**[{cand.get('domain', 'General')}]** `{cand.get('rule_type')}`")
                st.markdown(f"**模式**: {cand.get('pattern')}")
                st.markdown(f"**建议行动**: {cand.get('proposed_action')}")
                st.caption(f"时间: {cand.get('created_at')} | 注入扫描: {cand.get('prompt_injection_scan')}")
                
                c_col1, c_col2 = st.columns(2)
                with c_col1:
                    if st.button("✅ 晋级生产", key=f"promote_{cand['candidate_id']}", use_container_width=True):
                        with st.spinner("正在执行离线 Benchmark 回归测试与负迁移门禁核验..."):
                            prod_mem, msg = history_manager.promote_candidate(cand['candidate_id'], enforce_gate=True)
                            if prod_mem:
                                st.success(f"✅ 门禁通过！已晋级为生产级经验规则 ({prod_mem.version})！")
                                st.rerun()
                            else:
                                st.error(f"❌ 门禁拦截晋级: {msg}")
                with c_col2:
                    if st.button("❌ 驳回", key=f"reject_{cand['candidate_id']}", use_container_width=True):
                        history_manager.reject_candidate(cand['candidate_id'], notes="人工审核驳回")
                        st.info("已标记为拒绝。")
                        st.rerun()
                st.markdown("---")
        else:
            st.info("当前隔离区无待审经验。用户反馈修改后会自动进入隔离区。")

        # Active Production Memories View
        active_memories = history_manager.list_production_memories(active_only=True)
        st.markdown(f"**正式生产经验 ({len(active_memories)} 条)**:")
        for mem in active_memories:
            col_m_info, col_m_rb = st.columns([3, 1])
            with col_m_info:
                eval_badge = f" (得分: {mem.get('eval_score', 1.0)*100:.0f}%)" if mem.get('eval_score') else ""
                st.markdown(f"• **[{mem.get('domain')}]** {mem.get('pattern')[:25]}... $\\rightarrow$ *{mem.get('approved_action')[:25]}...* (`{mem.get('version')}`{eval_badge})")
            with col_m_rb:
                if st.button("↩️ 回滚", key=f"rb_{mem['memory_id']}", use_container_width=True):
                    history_manager.rollback_memory(mem['memory_id'], reason="管理员操作回滚")
                    st.info("已成功回滚注销该生产经验。")
                    st.rerun()

        # Benchmark Runner Button
        if st.button("⚡ 运行离线回归基线评测 (Benchmark)", use_container_width=True):
            with st.spinner("正在运行离线回归测试集并核验门禁..."):
                bench_res = benchmark_runner.run_benchmark_suite(verbose=False)
                st.session_state.benchmark_results = bench_res
                if bench_res["benchmark_passed"]:
                    st.success("✅ 离线回归评测全部达标 (Gate Passed)！")
                else:
                    st.error("❌ 离线回归存在未达标指标，触发门禁拦截。")

    if st.session_state.benchmark_results:
        with st.expander("📊 最新离线评测报告", expanded=False):
            b_data = st.session_state.benchmark_results
            st.write(f"**断言准确率 (Precision)**: {b_data['avg_claim_precision']*100:.1f}%")
            st.write(f"**引用支撑率 (Grounding)**: {b_data['avg_citation_grounding']*100:.1f}%")
            st.write(f"**缺口捕获率 (Gap Recall)**: {b_data['gap_detection_recall']*100:.1f}%")
            st.write(f"**门禁状态**: {'[ALL PASSED]' if b_data['benchmark_passed'] else '[REJECTED]'}")

    st.divider()

    # Research History Section
    st.subheader("📚 历史研究档案")
    
    if st.button("➕ 开启全新研究 (New)", use_container_width=True):
        st.session_state.active_session = None
        st.session_state.research_topic_val = ""
        st.rerun()

    saved_sessions = history_manager.list_sessions()
    if saved_sessions:
        st.caption(f"共存档 {len(saved_sessions)} 篇研究记录 (SQLite WAL 存储)")
        session_options = {
            f"{s['updated_at'][:16]} | {s['topic'][:18]}...": s["session_id"]
            for s in saved_sessions
        }
        selected_label = st.selectbox(
            "选择已保存的研究报告查看",
            options=list(session_options.keys()),
            index=0
        )
        selected_id = session_options[selected_label]
        
        col_load, col_del = st.columns([2, 1])
        with col_load:
            if st.button("📖 调入该研究", use_container_width=True):
                loaded = history_manager.get_session(selected_id)
                if loaded:
                    st.session_state.active_session = loaded
                    st.session_state.research_topic_val = loaded.get("topic", "")
                    st.success("已成功加载历史研究档案！")
                    st.rerun()
        with col_del:
            if st.button("🗑️ 删除", use_container_width=True):
                history_manager.delete_session(selected_id)
                if st.session_state.active_session and st.session_state.active_session.get("session_id") == selected_id:
                    st.session_state.active_session = None
                    st.session_state.research_topic_val = ""
                st.rerun()

        active_sess = st.session_state.active_session
        if active_sess and active_sess.get("session_id"):
            revisions = history_manager.list_report_revisions(active_sess["session_id"])
            if len(revisions) > 1:
                with st.expander(f"📜 报告版本历史 ({len(revisions)} 个快照)", expanded=False):
                    latest_rev_no = max(r["revision_no"] for r in revisions)
                    for r in reversed(revisions):
                        is_cur = (r["revision_no"] == latest_rev_no)
                        tag = " (当前)" if is_cur else ""
                        c_rev_txt, c_rev_btn = st.columns([3, 2])
                        with c_rev_txt:
                            st.caption(f"**v{r['revision_no']}{tag}**\n{r['created_at'][5:16]}")
                        with c_rev_btn:
                            if not is_cur:
                                if st.button("↩️ 恢复", key=f"sb_rev_rb_{r['revision_id']}", use_container_width=True):
                                    history_manager.rollback_to_revision(
                                        active_sess["session_id"],
                                        r["revision_id"],
                                        reason=f"侧边栏回滚至版本 v{r['revision_no']}"
                                    )
                                    st.session_state.active_session = history_manager.get_session(active_sess["session_id"])
                                    st.success(f"已恢复至版本 v{r['revision_no']}")
                                    st.rerun()
    else:
        st.info("暂无历史研究记录，完成第一次研究后将自动保存在此。")

# Main content header
st.title("📘 AI Deep Research Agent (Self-Evolving v2.0)")
st.markdown(
    """
    **生产级自进化智能体流水线**：📋 **契约规划** $\\rightarrow$ 🔍 **检索与证据账本** $\\rightarrow$ 🛡️ **审计验真与缺口补查** $\\rightarrow$ ✍️ **终稿深度合成**  
    遵循**证据先行**与**记忆准入隔离**原则，基于 SQLite WAL 沉淀 Claim-Evidence 证据链与自进化经验治理。
    """
)

# Research topic input
topic_input = st.text_input(
    "输入您想要研究的主题：", 
    value=st.session_state.research_topic_val,
    placeholder="例如：2026年具身智能机器人最新突破与商业落地"
)
if topic_input != st.session_state.research_topic_val:
    st.session_state.research_topic_val = topic_input

# Firecrawl deep_research tool with strict_mode=False
deep_research = create_deep_research_tool(
    api_key_getter=lambda: st.session_state.get("firecrawl_api_key", "")
)

def create_agents(api_key: str, base_url_str: str, model_name_str: str):
    """Dynamically create all agents configured with the selected model (Backward Compatibility)."""
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url_str.strip() if base_url_str and base_url_str.strip() else None
    )
    set_default_openai_client(client)
    model = OpenAIChatCompletionsModel(model=model_name_str.strip(), openai_client=client)
    search_tool = create_deep_research_tool(api_key_getter=lambda: st.session_state.get("firecrawl_api_key", ""))
    return (
        create_research_agent(model, tools=[search_tool]),
        create_elaboration_agent(model),
        create_verification_agent(model, tools=[search_tool])
    )

async def run_research_process(topic: str, api_key: str, base_url_str: str, model_name_str: str):
    """Run the complete 4-stage evidence-first research pipeline using ResearchWorkflow."""
    active_mems = history_manager.list_production_memories(active_only=True)
    rules_prompt = ""
    if active_mems:
        rules_prompt = "\n\n【系统生效中的生产级避坑指南与质量契约 (Active Production Rules)】:\n" + "\n".join([
            f"- [{m.get('domain')}] {m.get('pattern')}: {m.get('approved_action')}"
            for m in active_mems
        ])

    ctx = RunContext(
        topic=topic,
        llm_config={"provider": st.session_state.llm_provider, "model": model_name_str}
    )

    status_holder = st.empty()
    def on_progress(stage, msg):
        status_holder.info(f"**[{stage}]** {msg}")

    workflow = ResearchWorkflow(context=ctx, on_progress=on_progress)
    session_data = await workflow.run(
        topic=topic,
        api_key=api_key,
        base_url=base_url_str,
        model_name=model_name_str,
        firecrawl_key_getter=lambda: st.session_state.get("firecrawl_api_key", ""),
        active_rules_prompt=rules_prompt
    )
    status_holder.empty()
    return session_data

async def run_feedback_revision(
    session: Dict[str, Any], 
    user_feedback: str, 
    target_agent_type: str, 
    api_key: str, 
    base_url_str: str, 
    model_name_str: str
):
    """Route user feedback, capture quarantine candidate, and update report using ResearchWorkflow."""
    is_injected, scan_msg = scan_for_prompt_injection(user_feedback)
    if is_injected:
        st.warning(f"⚠️ 用户输入触发防投毒安全审计拦截: {scan_msg}")
        return session.get("final_report", ""), "安全拦截", None, None

    correction_signals = ["有误", "不对", "应该是", "纠正", "时间是", "发布于", "数据错", "不要", "避免", "wrong", "incorrect", "false", "mistake"]
    if any(sig in user_feedback.lower() for sig in correction_signals):
        history_manager.add_quarantine_candidate(
            pattern=f"用户针对课题 [{session.get('topic')[:20]}] 的指正: {user_feedback[:80]}",
            proposed_action=f"在核验阶段前置校验该事实: {user_feedback[:120]}",
            domain=session.get('topic', 'General')[:15],
            rule_type="mistake_avoidance",
            session_id=session.get('session_id'),
            prompt_injection_scan="passed",
            review_notes="由用户反馈自动沉淀至隔离区，待管理员审查晋级"
        )
        st.info("🛡️ 已将本次纠错经验安全存入【记忆隔离区】（待治理审核，未直接污染生产环境）。可在侧边栏审查晋级。")

    ctx = RunContext(topic=session.get("topic", ""))
    workflow = ResearchWorkflow(context=ctx)
    with st.spinner("正在处理您的修订意见并生成新版本..."):
        return await workflow.run_feedback_revision(
            session=session,
            user_feedback=user_feedback,
            target_agent_type=target_agent_type,
            api_key=api_key,
            base_url=base_url_str,
            model_name=model_name_str,
            firecrawl_key_getter=lambda: st.session_state.get("firecrawl_api_key", "")
        )

# Start research trigger
is_ready = bool(llm_api_key and firecrawl_api_key and topic_input and model_name)
if st.button("🚀 开始深度研究 (Start Research)", disabled=not is_ready, type="primary"):
    if not llm_api_key:
        st.warning(f"请在侧边栏填写 {provider_config['key_label']}。")
    elif not firecrawl_api_key:
        st.warning("请在侧边栏填写 Firecrawl API Key。")
    elif not topic_input:
        st.warning("请输入研究主题。")
    else:
        try:
            session_data = asyncio.run(
                run_research_process(topic_input, llm_api_key, base_url, model_name)
            )
            session_id = history_manager.save_session(session_data)
            session_data["session_id"] = session_id
            st.session_state.active_session = session_data
            st.success(f"✅ 四阶段自进化研究与事实核查完成！研究结果已归档至 SQLite 数据库。")
            st.rerun()
        except Exception as e:
            st.error(f"处理过程中出现错误: {str(e)}")

# Display Active Session & Feedback Loop
active_session = st.session_state.active_session
if active_session:
    st.divider()
    
    # Session Header
    header_col1, header_col2, header_col3 = st.columns([3, 1, 1])
    with header_col1:
        st.subheader(f"📌 研究课题：{active_session.get('topic')}")
        st.caption(f"创建时间: {active_session.get('created_at', '未知')} | 最近更新: {active_session.get('updated_at', '未知')} | 模型: {active_session.get('provider')} ({active_session.get('model')})")
    with header_col2:
        st.download_button(
            "📥 下载报告 (.md)",
            active_session.get("final_report", ""),
            file_name=f"{active_session.get('topic', 'research').replace(' ', '_')}_report.md",
            mime="text/markdown",
            use_container_width=True
        )
    with header_col3:
        json_export_str = history_manager.export_session(active_session.get("session_id", ""), format_type="json")
        st.download_button(
            "📥 导出档案 (.json)",
            json_export_str,
            file_name=f"{active_session.get('topic', 'research').replace(' ', '_')}_archive.json",
            mime="application/json",
            use_container_width=True
        )

    # Multi-Tab Report Views
    tab_final, tab_ledger, tab_audit, tab_elaboration, tab_initial, tab_history = st.tabs([
        "📑 最终核实报告",
        "📊 结构化证据账本 (Evidence Ledger)",
        "🛡️ 事实核查审计详情",
        "✍️ 深度拓展初稿",
        "🔍 原始调研初稿",
        "📜 修改修订历史"
    ])

    with tab_final:
        st.markdown(active_session.get("final_report", "暂无报告"))

    with tab_ledger:
        st.subheader("📊 结构化 Claim-Evidence 证据账本与时态分析")
        ledger_dict = active_session.get("evidence_ledger")
        if ledger_dict and "claims" in ledger_dict and ledger_dict["claims"]:
            claims_data = ledger_dict["claims"]
            m_col1, m_col2, m_col3 = st.columns(3)
            with m_col1:
                st.metric("总核准断言数 (Claims)", len(claims_data))
            with m_col2:
                corrob_count = sum(1 for c in claims_data if c.get("status") == "corroborated")
                st.metric("佐证达标率 (Corroborated)", f"{(corrob_count/len(claims_data))*100:.1f}%")
            with m_col3:
                avg_conf = sum(float(c.get("confidence", 1.0)) for c in claims_data) / len(claims_data)
                st.metric("平均置信度 (Avg Confidence)", f"{avg_conf*100:.1f}%")

            st.markdown("---")
            for idx, c in enumerate(claims_data, 1):
                status_icon = "✅" if c.get("status") == "corroborated" else ("⚠️" if c.get("status") == "disputed" else "❓")
                with st.expander(f"{status_icon} 断言 #{idx}: {c.get('statement')[:60]}... (置信度: {float(c.get('confidence', 1.0))*100:.0f}%)", expanded=(idx <= 2)):
                    st.markdown(f"**完整断言**: {c.get('statement')}")
                    st.markdown(f"**断言类型**: `{c.get('claim_type')}` | **状态**: `{c.get('status')}`")
                    
                    scope_info = c.get("scope") or {}
                    if scope_info.get("product") or scope_info.get("release_stage"):
                        st.markdown(f"**适用口径与阶段**: 产品 `{scope_info.get('product')}` / 阶段 `{scope_info.get('release_stage')}`")
                        
                    valid_time = c.get("valid_time") or {}
                    if valid_time.get("effective_from"):
                        st.markdown(f"**时态生效期**: 从 `{valid_time.get('effective_from')}` 起有效")

                    ev_list = c.get("evidence") or []
                    if ev_list:
                        st.markdown("**支撑证据链与立场 (Evidence & Stance)**:")
                        for ev in ev_list:
                            st.markdown(f"- [{ev.get('title') or ev.get('url')}]({ev.get('url')}) — 立场: `{ev.get('stance')}` | 来源属性: `{ev.get('source_type')}`")
                    else:
                        st.caption("暂无显式外部引用链接。")
        else:
            st.info("当前会话暂无结构化证据账本数据。")

    with tab_audit:
        st.info("以下为审计智能体 (Verification Agent) 输出的完整事实核查与可信度审计报告：")
        st.markdown(active_session.get("verified_report", "暂无核查详情"))

    with tab_elaboration:
        st.markdown(active_session.get("enhanced_report", "暂无拓展报告"))

    with tab_initial:
        st.markdown(active_session.get("initial_report", "暂无初版报告"))

    with tab_history:
        st.subheader("📜 报告版本快照与历史回滚 (Immutable Versioning)")
        active_sess_id = active_session.get("session_id")
        revisions = history_manager.list_report_revisions(active_sess_id) if active_sess_id else []
        if revisions:
            st.caption(f"底层 `report_versions` 关系表已持久化 {len(revisions)} 个不可变快照，支持原子级回滚与证据溯源。")
            latest_rev_no = max(r["revision_no"] for r in revisions)
            for rev in reversed(revisions):
                is_cur = (rev["revision_no"] == latest_rev_no)
                badge = " 🟢 (当前活跃版本)" if is_cur else ""
                with st.expander(f"📌 版本 v{rev['revision_no']}{badge} — {rev.get('created_at', '')} | 动因: {rev.get('change_reason', '初始生成')}", expanded=is_cur):
                    col_r_info, col_r_act = st.columns([3, 1])
                    with col_r_info:
                        st.markdown(f"**版本 ID**: `{rev['revision_id']}`")
                        if rev.get("parent_revision_id"):
                            st.markdown(f"**父版本 ID**: `{rev['parent_revision_id']}`")
                        st.markdown(f"**变更动因**: {rev.get('change_reason', '初始生成')}")
                    with col_r_act:
                        if not is_cur:
                            if st.button("↩️ 恢复此版本", key=f"tab_rb_{rev['revision_id']}", use_container_width=True):
                                rolled_back = history_manager.rollback_to_revision(
                                    active_sess_id,
                                    rev['revision_id'],
                                    reason=f"通过 UI 回滚至版本 v{rev['revision_no']}"
                                )
                                if rolled_back:
                                    st.session_state.active_session = history_manager.get_session(active_sess_id)
                                    st.success(f"✅ 已成功原子回滚至版本 v{rev['revision_no']}！")
                                    st.rerun()
                                else:
                                    st.error("回滚失败，请检查数据库状态。")

                    st.markdown("**该版本终稿快照摘要**:")
                    st.markdown(rev.get("final_report", "")[:600] + ("..." if len(rev.get("final_report", "")) > 600 else ""))

                    # Render Verdict Diff if present
                    diff_data = rev.get("verdict_diff") or {}
                    if diff_data.get("has_changes") and diff_data.get("changes"):
                        st.markdown("##### 🔍 事实状态更迭对比 (Verdict Diff):")
                        diff_rows = []
                        for ch in diff_data["changes"]:
                            diff_rows.append({
                                "断言陈述 (Statement)": ch["statement"][:60] + ("..." if len(ch["statement"]) > 60 else ""),
                                "修订前状态": ch["old_status"],
                                "修订后状态": ch["new_status"],
                                "更迭类型": "状态迁移" if ch.get("type") == "transition" else "新增提取断言"
                            })
                        st.dataframe(diff_rows, use_container_width=True)
                    elif rev.get("revision_no", 1) > 1:
                        st.caption("ℹ️ 本次修订各断言的事实核查状态保持一致，未发生状态迁移。")

                    verif_raw = rev.get("verification_snapshot_json")
                    if verif_raw:
                        try:
                            v_list = json.loads(verif_raw)
                            if v_list:
                                st.caption(f"🛡️ 包含 {len(v_list)} 条结构化核查审计 (Verification Results)")
                        except Exception:
                            pass
            st.markdown("---")

        feedback_list = active_session.get("feedback_history", [])
        if feedback_list:
            st.markdown(f"**累计修订交互记录**: {len(feedback_list)} 次")
            for idx, fb in enumerate(reversed(feedback_list), start=1):
                with st.expander(f"修订 #{len(feedback_list) - idx + 1} - {fb.get('timestamp')} | 处理智能体: {fb.get('target_agent')}"):
                    st.markdown(f"**用户修改意见**: \n> {fb.get('user_request')}")
                    st.markdown("**修订后报告摘要/内容**:")
                    st.markdown(fb.get("revised_report", "")[:1000] + ("..." if len(fb.get("revised_report", "")) > 1000 else ""))
        elif not revisions:
            st.info("当前报告尚未提交任何修改意见。您可以在下方提交修改请求，启动反馈修订循环。")

    # Feedback Revision Section
    st.divider()
    st.subheader("💬 针对报告提供修改意见 (User Feedback Loop)")
    st.markdown("您可以对报告提出增补、修改或质疑。系统会**智能路由**并**将可沉淀的纠错经验安全存入隔离区**：")
    
    col_fb_text, col_fb_route = st.columns([3, 1])
    with col_fb_text:
        user_revision_input = st.text_area(
            "修改需求说明：", 
            placeholder="例如：\n- 补充最新商业化落地案例（将路由给拓展智能体）\n- 检索最新的融资与市场份额数据（将路由给调研智能体）\n- 文中关于发布时间有误，实际为2025年末（将安全沉淀至隔离区并核实）",
            height=100
        )
    with col_fb_route:
        route_options = [
            "🤖 智能自动路由 (Auto-Route)",
            "🔍 调研智能体 (Research Agent)",
            "✍️ 拓展智能体 (Elaboration Agent)",
            "🛡️ 事实核查智能体 (Verification Agent)"
        ]
        selected_route = st.selectbox("指派修订智能体", options=route_options)
        submit_feedback = st.button("🚀 提交修改请求 (Submit Revision)", type="primary", use_container_width=True)

    if submit_feedback:
        if not user_revision_input.strip():
            st.warning("请输入具体的修改需求说明。")
        elif not llm_api_key:
            st.warning("请在左侧侧边栏提供大模型 API Key 以便智能体进行修订。")
        else:
            if "智能自动路由" in selected_route:
                target_agent_type = classify_feedback_intent(user_revision_input)
            elif "调研智能体" in selected_route:
                target_agent_type = "research_agent"
            elif "拓展智能体" in selected_route:
                target_agent_type = "elaboration_agent"
            else:
                target_agent_type = "verification_agent"
                
            try:
                revised_report, agent_display_name, new_ledger, new_verification_results = asyncio.run(
                    run_feedback_revision(
                        active_session, 
                        user_revision_input, 
                        target_agent_type, 
                        llm_api_key, 
                        base_url, 
                        model_name
                    )
                )
                
                # Update in SQLite with atomic ledger & verification sync
                session_id = active_session.get("session_id")
                updated_session = history_manager.append_feedback_to_session(
                    session_id=session_id,
                    user_request=user_revision_input,
                    target_agent=agent_display_name,
                    revised_report=revised_report,
                    new_ledger=new_ledger,
                    verification_results=new_verification_results
                )
                if updated_session:
                    st.session_state.active_session = updated_session
                else:
                    active_session["final_report"] = revised_report
                    if new_ledger:
                        active_session["evidence_ledger"] = new_ledger
                    st.session_state.active_session = active_session
                    
                st.success(f"✅ 修订成功！已由【{agent_display_name}】完成修改并同步到 SQLite 数据库。")
                st.rerun()
            except Exception as e:
                st.error(f"修改处理失败: {str(e)}")

# Footer
st.markdown("---")
st.caption("AI Deep Research Agent v2.0 • Self-Evolving & Evidence-First Architecture with SQLite WAL & Quarantine Governance")
