# IMPROVEMENT_PLAN_V4 — 从治理原型到可信系统

本文是 V3 自评之后的第四轮改进计划。V3 留下的判断是：项目已具备"证据校验、版本化、运行治理"的骨架（作品集 8.5/10），但有三个关键断点——线上 corpus 未绑定、负迁移评测是模拟、事实修订不重验证。V4 的目标只有一句话：**把这三条断链焊上，再补齐工程化基线，使项目从"能讲架构"升级为"能证明可信"。**

写作本文时对照了三份在招岗位 JD（字节 AI Platform「Agent开发工程师」、腾讯 IEG「Agent开发工程师」、阿里「Agent Infra工程师」），每项改进都标注了它对应的岗位信号，避免做与求职方向无关的过度工程。

## 0. 当前状态快照（2026-09）

| 维度 | 现状 | 依据 |
|------|------|------|
| 代码规模 | 生产代码约 4,100 行 + 评测 680 行 + 测试约 1,500 行（15 个测试文件） | PowerShell 统计，排除 .venv |
| 测试 | V3 记录 49 tests OK；`.coverage` 存在，`fail_under=70` | `.coveragerc` |
| 核心断点 1 | `core/workflow.py` 导入 `build_corpus_from_sources` 但从未调用；线上 `recompute_all_statuses()` 与 `verify_claims_deterministically()` 均未传 corpus | V3 自评第 1 条，代码复核属实 |
| 核心断点 2 | `evaluation/runner.py` 差分回归是"复制 baseline + 危险词检测 + 手动改指标"，candidate 未真实注入 workflow | V3 自评第 2 条 |
| 核心断点 3 | `storage.py` 的 `append_feedback_to_session()` 创建新版本时复制旧 ledger 快照、`verification_snapshot_json=None`，事实性修改不触发重验证 | V3 自评第 3 条 |
| 工程化 | 无 CI、无 `pyproject.toml`、`requirements.txt` 未锁定版本；本机 `.venv` 缺 pytest（复现：`No module named pytest`） | 实测 |
| 评测集 | `cases.jsonl` 仅 4 条，Mock LLM 只生成理想输出 | 代码复核 |
| 成本预算 | `BudgetConfig.max_cost_usd` 只有字段，运行期无检查 | 代码复核 |

与岗位 JD 的对照结论（V4 的立项依据）：

- 字节 JD 原文要求"设计并落地 Agent 自进化全链路体系"——项目的 quarantine→benchmark→promote 生命周期正中靶心，但门禁目前可被绕过且差分是假的，面试深挖必塌。
- 腾讯 JD 要求"建设 Agent Benchmark、评测与可观测体系，通过自动化评测、回归测试、反馈闭环持续优化"——评测闭环的三个断点直接对应。
- 三家 JD 均点名 MCP、CI/CD、可观测性——项目零覆盖或覆盖不足。

## 1. P0-1 打通线上 EvidenceCorpus 全链路

**优先级最高的原因**：这是唯一会被一个追问击穿的点。"你的 Citation Validator 在线上是怎么生效的？"——当前真实答案是"没生效"。评测路径传了 corpus，线上路径没传，等于系统存在两套验证逻辑，一套严格一套宽松。

### 问题定位

`core/workflow.py` 中：

```python
# 已导入，未调用
from schemas import build_corpus_from_sources  # 实际情况：导入存在，调用缺失
```

三个缺口：

1. Stage 1 搜索结果没有构建 corpus，`evidence_ledger.recompute_all_statuses()` 无 corpus 参数；
2. Stage 2 缺口补查新增的 `EvidenceDocument` 没有进入 corpus，补查证据不参与状态计算；
3. Stage 4 `verify_claims_deterministically(evidence_ledger.claims, model_proposals=...)` 缺 corpus 参数，`Claim.recalculate_status_and_confidence(corpus=None)` 时 URL 存在性检查被跳过。

### 修改方案

涉及文件：`core/workflow.py`、`storage.py`、`schemas.py`（不改签名，只补调用）。

1. **Stage 1 后**：搜索返回的 sources 先经 `build_corpus_from_sources()` 构建 corpus，再交给 `parse_evidence_ledger_from_output()`，随后 `evidence_ledger.recompute_all_statuses(corpus=corpus)`。
2. **Stage 2 中**：每次缺口补查得到的新文档，先 `corpus.add_document(doc)` 再参与 gap 判定与状态重算，保证"新增证据必须先入池、再参与裁决"。
3. **Stage 4**：`verify_claims_deterministically(claims, corpus=corpus, model_proposals=...)`。
4. **持久化**：`report_versions` 表新增 `corpus_snapshot_json` 列（或复用现有 JSON 快照机制），每次 revision 保存 corpus 快照，为 P0-2 提供输入。
5. **反哺修订**：`deep_research_openai.py` 的反馈修订入口在拿到 corpus 快照后同样传参。

### 验收标准

新增测试（建议放 `tests/test_corpus_binding.py`）：

- `test_workflow_marks_out_of_corpus_citation_unverified`：构造一条引用"格式合法但不在 corpus"的 URL 的 claim，跑完 Stage 1→4 后断言 verdict 为 `unverified` 且 reasoning 含 corpus 检查说明。这是**先写失败的测试**，改代码前它应当红。
- `test_gap_search_evidence_joins_corpus_before_recompute`：Stage 2 补查的文档出现在最终 corpus 快照中。
- `test_revision_snapshot_contains_corpus`：revision 记录含非空 corpus 快照。
- 现有 49 个测试全部保持绿。

工作量估计：1~1.5 天（含写失败测试）。

## 2. P0-2 事实型反馈修订的自动重验证

**岗位信号**：腾讯 JD"反馈闭环"、字节 JD"基于业务反馈的持续能力升级"。

### 问题定位

`storage.py` 的 `append_feedback_to_session()`：用户修订报告后，新 revision 的 ledger 是旧快照的复制，`verification_snapshot_json=None`。用户改一个日期、一个数字、一个发布状态，系统不会重新提取 claim、不会重新校验、不会生成新的 VerificationResult。当前实现是"报告版本化"，不是"事实资产版本化"。

### 修改方案

前置依赖：P0-1 的 corpus 快照。

1. **意图分流**：反馈入口已有关键词检测（"有误/不对/应该是/纠正/时间/数据错"）。将检测到事实性纠错的反馈路由到"全链路重验证"路径，而非普通润色路径。
2. **重验证路径**：对修订后报告重新执行 `parse_evidence_ledger_from_output()` → `recompute_all_statuses(corpus=corpus_snapshot)` → `verify_claims_deterministically(...)`，生成新的 `VerificationResult[]`。
3. **差分展示**：在 revision 记录中保存前后 verdict 变化（哪条 claim 从 corroborated 变 disputed），Streamlit 的 Revision History 面板直接渲染这个 diff。这是面试演示时最有说服力的一屏。
4. **可选补查**：若重验证发现新增 unverified/disputed claim，走一次 Stage 2 缺口补查（复用现有逻辑，预算内）。

### 验收标准

新增测试（`tests/test_feedback_reverification.py`）：

- `test_factual_feedback_triggers_reverification`：修订文本中修改某 claim 的日期后，新 revision 的 verification snapshot 非空且对应 claim verdict 发生变化。
- `test_cosmetic_feedback_skips_reverification`：纯润色反馈（无事实纠错关键词）不触发重跑，成本不增加。
- `test_revision_verdict_diff_rendered`：diff 结构可被 UI 层渲染。

工作量估计：1~1.5 天。

## 3. P1-1 真实差分回归（candidate 记忆注入 workflow）

**岗位信号**：字节 JD"自进化全链路"；腾讯 JD"回归测试"；本项目 README 宣称的 "Differential Benchmark" 目前名不副实。

### 问题定位

`evaluation/runner.py` 的当前逻辑：复制 baseline 指标 → 检测 candidate 规则文本中的危险词 → 手动把 false corroboration 写成 0.5、gap recall 写成 0。这不是回归测试，是规则文本扫描。V3 的表述是准确的："面试时只能说已实现差分评测框架和高风险策略拦截"。

### 修改方案

核心动作：**让 candidate 记忆真实改变 workflow 行为，再对比两次真实运行**。

1. **注入点**：在 `core/workflow.py` 增加 memory 加载层——运行时从 `production_memories` 读取激活规则，拼入 Research Agent 与 Verification Agent 的 instructions（当前生产记忆没有任何进入线上 prompt 的通道，这本身就是一个应修复的断点）。
2. **双分支执行**：`runner.py` 对每个 case 跑两次完整 mock workflow——baseline（`memory=[]`）与 candidate（`memory=[candidate_rule]`），逐 case 比对两分支的指标。
3. **Mock 协同**：`evaluation/mock_services.py` 的 MockLLMExtractor 参数化——当注入的 memory 规则与 case 主题相关时，模拟该规则对模型输出的影响（例如注入"优先采信一手信源"的规则后，mock 输出的 stance 分布改变）。这一步是诚实标签的关键：mock 模拟的是"规则影响"，不再是"直接改指标"。
4. **门禁定义**：candidate 分支必须在 5 项绝对门禁内，且相对 baseline 无退化（每项指标劣化幅度 < 2 个百分点）。
5. 保留现有危险词扫描作为快速预检（成本低的粗筛），但晋级判定只看真实差分结果。

### 验收标准

- `test_real_differential_regression`：构造一条"会诱导模型忽略反驳证据"的 candidate 规则，断言 candidate 分支 gap recall 显著低于 baseline，且被 gate 拦截。
- `test_helpful_rule_passes_differential`：构造一条中性增益规则（如"引用必须带 snippet"），断言双分支均过门禁。
- `runner.py` 输出报告包含双分支逐 case 指标表。

工作量估计：2 天。**完成此项之前，不要在简历或 README 中写"已实现差分回归"以外的更强表述。**

## 4. P1-2 对抗性 Benchmark 扩充（4 → 12+ 条）

**岗位信号**：三家 JD 都点名 Agent 评测；字节专门有 Agent 评测团队。

### 问题定位

`cases.jsonl` 现有 4 条用例（Marble 时间线、DeepSeek-V3 成本、GPT-5 传闻、Genie 3 时间线）全部验证"理想输入能被正确处理"。一个只喂正确答案的评测集无法证明系统的拒绝能力。

### 修改方案

`mock_services.py` 增加故障注入参数（`fault_mode`），每条新 case 声明一种故障：

| 新增 case 类型 | fault_mode | 期望行为 |
|----------------|-----------|----------|
| 伪造 URL（格式合法但不在 corpus） | `fake_url` | 该 claim 判 unverified |
| 日期冲突（证据 2024，断言 2026） | `date_conflict` | 判 disputed 或 unverified |
| 语义不匹配（URL 真实但内容无关） | `semantic_mismatch` | token overlap 检查触发 |
| ledger JSON 缺失/损坏 | `malformed_json` | 走解析失败降级路径，不崩溃 |
| 网页文本注入攻击 | `injected_snippet` | `scan_for_prompt_injection` 拦截 |
| stance 标注错误（支持标成反驳） | `wrong_stance` | 状态机与 stance 交叉校验发现 |
| 冲突来源（一支持一反驳） | `conflicting_sources` | 判 disputed，置信度下调 |

每条 case 在 `ground_truth` 中写明每个 claim 的期望 verdict，指标计算按 verdict 一致率统计。

### 验收标准

- `cases.jsonl` ≥ 12 条，7 种故障类型全覆盖；
- `python -m evaluation.runner` 离线全绿（当前 5 门禁阈值不变）；
- 新增 `test_adversarial_cases_all_rejected`：所有故障 case 中被污染的 claim 不得出现 `corroborated`。

工作量估计：1~1.5 天（主要是 fixture 编写）。

## 5. P1-3 工程化基线（CI、依赖、成本预算）

**岗位信号**：阿里 JD"测试与变更管理（CI/静态分析）"；腾讯 JD"CI/CD、自动化测试"。

### 问题清单与方案

1. **`pyproject.toml`**：迁移项目元数据与依赖声明，`requirements.txt` 保留为由 `uv pip compile`（或 `pip-compile`）生成的锁定文件。解决"本机 `.venv` 缺 pytest"这类环境不可复现问题——这是实测踩到的坑，面试官如果 clone 项目跑不起来，一切归零。
2. **GitHub Actions**：单 workflow，push/PR 触发；Python 3.10 与 3.11 双矩阵；步骤为 `pip install -r requirements.lock` → `pytest --cov` → 覆盖率不低于 `.coveragerc` 的 70%。不需要更复杂的流水线，这个项目的体量配一条 CI 就够。
3. **pytest markers 落地**：`pytest.ini` 已定义 `unit/integration/benchmark/safety` 四个 marker 但无测试使用。给 15 个测试文件补 `@pytest.mark` 标注，CI 默认跑全部，本地可 `pytest -m unit` 快速迭代。
4. **成本预算运行期强制**：`BudgetConfig.max_cost_usd` 目前是死字段。在 `RunContext` 中补 `track_cost(tokens, price_per_token)`，价格表放 `config_manager.py` 按 provider 配置；`track_tokens()` 内部同步累计成本，超限抛 `BudgetExceededError`（复用现有异常与测试模式）。
5. **README 更新**：补 corpus 机制、benchmark 边界、离线评测命令、环境搭建步骤。V3 第 7 条已列，此处不展开。

### 验收标准

- 干净虚拟环境按 README 一步装齐、测试全绿（本人本机验证过当前做不到）；
- CI 徽章绿；PR 上能看到覆盖率报告；
- `test_cost_budget_enforced`：构造低价高价两次调用，断言高价路径在预算处中断。

工作量估计：1 天。

## 6. P2-1 MCP 工具接入

**岗位信号**：三家 JD 全部点名 MCP（腾讯 JD 原文"通过 API、SDK、MCP 等方式建设集成"）。

### 方案（控制范围，1 天量级）

不追求把本项目做成 MCP server，只做**客户端接入**：

1. `core/tools/` 新增 `mcp_tools.py`：用 MCP Python SDK 的 stdio client 连接一个外部 server（推荐 Firecrawl 官方 MCP 或一个本地 echo/search server）；
2. 将 MCP 工具注册进 Research Agent 的 tools 列表，与现有 Firecrawl 直连工具并存（MCP 失败自动降级到直连，复用 `resilience.py` 的 retry/timeout）；
3. 测试用 fixture 启动一个最小 stdio MCP server（测试内置，不依赖网络）。

### 验收标准

- `test_mcp_tool_registration`：工具列表中出现 MCP 工具；
- `test_mcp_failure_falls_back`：MCP server 不可达时 workflow 正常完成（降级路径）。

## 7. P2-2 Verification Agent 结构化输出为主契约

### 问题定位

V3 第 4 条：Verification Agent 输出 Markdown 审计文本 + 末尾 JSON proposal block。结构化结果实际来自确定性引擎，模型侧的验证能力没有真正进入结构化闭环。

### 方案

1. Agent 输出以 `json:verification_proposals` 为主契约（Agents SDK 的 structured output 能力），Markdown 审计报告改为**由 `VerificationResult[]` 程序渲染**（项目已有 `render` 相关工具函数），模型 Markdown 仅作参考附录；
2. 解析失败（`parse_verification_proposals_from_text` 返回空）时记录 warning 并走纯规则路径——这个降级已存在，补测试固化。

工作量估计：0.5~1 天。

## 8. P2-3 stance 与一手来源判定升级

### 问题定位

Stage 2 的 stance 推断依据：否定关键词、token overlap、URL 是否含 `blog/news/press`。`is_first_party` 由 URL 子串推测。这些启发式不能叫语义验证，V3 第 5 条已自我定位准确。

### 方案（务实的第一版升级，不引模型）

1. `AUTHORITATIVE_FIRST_PARTY_DOMAINS` 从 `workflow.py` 模块级常量迁到 `config_manager.py` 可配置项，`is_first_party` 只认这个 allowlist，URL 子串推断删除；
2. stance 判定要求"snippet 与 claim 的 token overlap 达阈值 且 否定词出现在 claim 关键 token 的窗口内"，达不到一律 `neutral/unknown`；
3. 日期/数字约束：claim 中的年份、金额与 snippet 中出现的数值做一致性检查，冲突即标 disputed（该逻辑 CitationValidator 已有雏形，补齐调用）。

### 验收标准

- `test_first_party_requires_allowlist`：非 allowlist 域名的 `is_first_party` 恒为 False；
- `test_weak_overlap_defaults_neutral`：低相关证据 stance 为 neutral 而非 supports。

工作量估计：1 天。

## 9. 明确不做什么

克制与补强同样重要，以下方向评估后放弃：

- **不追阿里 Infra 岗的技术栈**（沙箱、K8s、高并发调度、Go/Rust）。本项目定位是应用层 Agent 工程，为 Infra 岗改造等于重写，且目标岗位错配。面试遇到 Infra 岗直接不投或如实说明方向差异。
- **不做多用户/分布式**。`docs/SYSTEM_DESIGN.md` 中的 FastAPI + Celery + PostgreSQL 设计保留为 roadmap 章节，简历中表述为"已完成单机架构，分布式方案有完整设计文档"，不冒充已实现。
- **不迁移 LangGraph**。OpenAI Agents SDK 与 LangGraph 是选型差异不是能力差距，迁移无净收益；但面试前要准备一段 30 秒的对比陈述（SDK 轻量、handoff 模型清晰 vs LangGraph 图编排、状态机持久化更强）。
- **不引入向量库中间件**。`core/rag/` 的 SQLite 向量存储对当前数据量够用。

## 10. 排期与依赖关系

按"每完成一项先跑全量回归再进下一项"的节奏（避免多改动叠加后失败归因困难）：

| 顺序 | 项 | 量级 | 前置依赖 |
|------|-----|------|----------|
| 1 | P0-1 corpus 全链路 | 1~1.5 天 | 无 |
| 2 | P0-2 事实修订重验证 | 1~1.5 天 | P0-1（corpus 快照） |
| 3 | P1-3 工程化基线 | 1 天 | 无（可与 P0 并行，建议先做 CI 保住已有成果） |
| 4 | P1-1 真实差分回归 | 2 天 | P0-1 |
| 5 | P1-2 对抗用例扩充 | 1~1.5 天 | P1-1（故障注入框架） |
| 6 | P2-1 MCP | 1 天 | 无 |
| 7 | P2-2 结构化验证输出 | 0.5~1 天 | 无 |
| 8 | P2-3 stance 升级 | 1 天 | P1-2（用例先行） |

关键路径：P0-1 → P0-2 → P1-1 → P1-2，合计约 6 个工作日。完成后门禁强制化（`promote_candidate` 移除 `enforce_gate=False` 的旁路，或旁路时在 UI 与日志中显式留痕）才有意义，作为 P1-1 的收尾动作一并处理。

全部 8 项约 9~10 个工作日。若只有 3 天预算，做 P0-1、P1-3、P1-2 中的对抗用例部分（前文评估中"3 天版本"），收益/成本比最高。

## 11. 面试叙事与诚实边界

每轮改进文档都以"面试时怎么说"收尾，V4 不例外。

### 完成后的能力表述（简历/自述用）

> 构建了证据先行的深度研究 Agent：Claim-Evidence 结构化事实模型、LLM 提议与确定性规则仲裁分离的双层验证、记忆隔离区与差分回归门禁构成的自进化治理、SQLite WAL 不可变版本化与回滚、熔断/退避/三维预算的运行治理、12+ 对抗用例的离线评测集，CI 全绿。

### 三个必被追问的问题与答法

1. **"引用校验线上怎么生效的？"**（P0-1 完成后）答：corpus 是封闭集合，每次搜索的文档先入池，所有状态计算与验证都传 corpus，引用 URL 不在池内一律 unverified。可以现场画出数据流。
2. **"负迁移率怎么测的？"**（P1-1 完成后）答：双分支真实运行，baseline 空 memory、candidate 注入规则，逐 case 比对，门禁含绝对阈值与相对退化阈值。
3. **"为什么不用 LangGraph？"** 答：编排是串行四阶段 + 共享 corpus，不需要图状态机；Agents SDK 的 handoff 与工具注册够用且更轻。准备好对比陈述。

### 仍未完成、被问到时的诚实回答

- 多用户并发与分布式部署：有设计文档（`docs/SYSTEM_DESIGN.md`），未实现，当前是单机单用户；
- 语义级 stance 验证：当前是规则启发式 + allowlist，不是 NLI 模型验证；
- 评测集规模：12 条对抗用例，不是大规模生产回归集。

这三个边界主动说出来，比被面试官挖出来强。V3 文档已经证明了这一点：诚实标注缺口的文档本身就是工程成熟度的证据。
