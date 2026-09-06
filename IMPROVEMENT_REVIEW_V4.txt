# IMPROVEMENT_REVIEW_V4 — V4 落地审计与再评估（2026-09-07）

本文是 IMPROVEMENT_PLAN_V4 发布后的落地审计：逐项核验八项改进的真实代码状态，修正评估中的过期判断，并给出更新后的岗位竞争力结论。审计方法为三路并行代码审查（P0/P1/P2 各一路，合计 95 次工具调用、逐行核验关键链路）+ 测试缓存取证 + 对 Stage 2 corpus 链路与 MCP 接线的人工复核。

结论先行：V4 计划的八项改进，四项完成、两项完成大半、两项未做；原先"会被一问击穿"的三个塌方点已闭合两个半。项目当前最大的缺口不在代码，而在于它还不是 git 仓库——CI 写了但从未运行过一次。

## 0. 一处诚实更正

时间戳证据显示：`core/workflow.py`（最后修改 09-06 21:11:57）、`evaluation/runner.py`（21:03:47）等核心文件的修改时间早于 V4 文档的创建时间（09-07 00:01:45），而 `mock_services.py`（09-07 01:04:09）晚于它。

这说明一个事实：V4 文档撰写时依据的是 09-06 下午的代码快照，而当天 19:00 至 21:12 之间已经完成了一轮大规模改造。因此 V4 中"P0-1 corpus 未绑定线上 workflow"的判断有部分基于过期快照。本轮逐行核实后的修正结论见第 1 节。

这个更正本身值得记录：评估文档如果不写明审计基准时间，就会像无时态的事实一样失效。V4 第 36 行还有一处笔误——`build_corpus_from_sources` 的导入位置是 `core/tools/search_tools.py`，不是 `schemas.py`。

## 1. 三条原断链的核验结果

### 1.1 断点一：线上 corpus 未绑定 → 已修复，链路完整

逐行证据（文件:行号）：

- Stage 1：`workflow.py:101-112` 将各 claim 的证据同步入池（`corpus.add_document()`），`:113` `ledger.recompute_all_statuses(corpus=corpus)`
- Stage 2：`search_tools.py:85-92、140-142` 搜索工具在每次调用内部自动把抓取结果 `corpus.add_document()`，因此缺口补查的证据先入池、再经 `workflow.py:196/:222` 带 corpus 重算。审计中专门复核了这条链路——此前"补查证据不进池"的疑虑不成立
- Stage 4：`workflow.py:310-313` `verify_claims_deterministically(..., corpus=corpus, model_proposals=...)`；`:330` 保底路径同样传 corpus
- 反馈修订：`workflow.py:437-442` 重解析 ledger → 带 corpus 重算 → 确定性验证

"评测路径严格、线上路径宽松"的两套逻辑问题已消除。`build_corpus_from_sources` 在 `workflow.py:18` 导入但从未调用，属于死导入，不影响功能，建议顺手清理。

等效测试已存在：`test_closed_loops_v3.py` 的 `test_corpus_grounding_enforcement` 覆盖了"不在池内的引用判 unverified"。

### 1.2 断点二：差分回归是模拟 → 已换成真实双分支

- `runner.py:131-157`：baseline 分支（`candidate_rule=None`）与 candidate 分支（注入规则）分别执行 mock workflow，经过真实 parser、真实状态机、真实指标计算。旧版"复制指标后手动改数值"的路径已删除
- `workflow.py:66-89`：`active_rules_prompt` 参数进入 `run()`；`deep_research_openai.py:345-369` 运行时从 `production_memories` 读取激活规则拼入 Research Agent 的 prompt。**记忆进入线上行为的通道此前完全不存在，现在打通了**——"记忆→影响行为→差分验证"的自进化闭环成立
- `mock_services.py:67-76`：candidate rule 参数化，对抗性规则（含 skip verification / ignore evidence 等关键词）会诱导 mock 生成强制 corroborated 的输出，从而真实触发门禁拦截
- 门禁：5 项绝对阈值 + 负迁移率 < 0.05。V4 规格写的是相对退化 < 2 个百分点（即 0.02），当前实现为 0.05，是一处小偏差，可在下一轮收紧
- `storage.py:657-672`：`promote_candidate` 默认 `enforce_gate=True`，UI 按钮（`deep_research_openai.py:191`）显式传 True。`enforce_gate=False` 旁路仍存在且无留痕，V4 要求的"旁路显式留痕"未做

保留的诚实边界：mock 对"规则影响输出"的模拟依赖关键词识别对抗模式，不是真实的模型行为仿真。面试时如实说明"差分框架是真实的、规则影响是模拟的"。

### 1.3 断点三：事实修订不重验证 → 主体已修复

- `workflow.py:413-442`（`run_feedback_revision`）：修订后报告重新 `parse_evidence_ledger_from_output()` → `recompute_all_statuses(corpus=corpus)` → `verify_claims_deterministically(...)`，生成新 `VerificationResult[]`
- `deep_research_openai.py:620-641`：新 ledger 与验证结果随 revision 持久化
- `deep_research_openai.py:388-398`：纠错关键词检测触发 quarantine 沉淀

未完成部分：revision 无 `verdict_diff` 字段，UI 的 Revision History 面板不渲染前后 verdict 变化。这是演示时最有说服力的一屏，建议补上。

## 2. V4 八项落地清单

| 项 | 状态 | 证据摘要 |
|---|---|---|
| P0-1 corpus 全链路 | 完成（主体先于 V4 文档完成） | 1.1 节逐行证据 |
| P0-2 修订重验证 | 完成约 80% | 重验证链路完整；缺 verdict diff 字段与 UI 渲染 |
| P1-1 真实差分回归 | 完成 | 双分支执行 + 记忆注入通道 + mock 参数化；旁路留痕与 0.02 阈值未做 |
| P1-2 对抗用例扩充 | 完成 | 12 条（4→12），fault_mode 共 10 种；V4 要求的 `wrong_stance` 未覆盖（以 `wrong_first_party` 替代） |
| P1-3 工程化基线 | 半成 | `track_cost()` + 价格表 + 三维预算强制已实现（`context.py:126-182`）；CI 配置已写；无 pyproject.toml、pytest markers 未标注到测试、requirements.txt 未显式声明 pydantic |
| P2-1 MCP 接入 | 半成 | `core/tools/mcp_tools.py` 客户端完整（JSON-RPC 握手/list/call + fallback 适配器），3 个测试全走 Mock 不依赖网络；**`create_mcp_tool_adapter` 在应用层零调用，未接入 Research Agent 工具列表** |
| P2-2 结构化验证输出 | 未做 | 仍为 Markdown 审计 + 末尾 JSON block 模式 |
| P2-3 stance 判定升级 | 未做 | 仍为关键词计数启发式；`AUTHORITATIVE_FIRST_PARTY_DOMAINS` 仍在 workflow.py，未迁 config_manager |

## 3. 测试取证

`.pytest_cache/v/cache/nodeids` 记录的最后一次完整运行时间为 09-07 01:05:38，共 **58 个测试**（V3 时代为 49），分布在 13 个测试文件中；缓存中无 `lastfailed` 文件，即该次运行全部通过。

必须同时记录的限定条件：本机四个 Python（3.10/3.12/3.13/3.14）均未安装 pytest，项目 `.venv` 是无 pip 的空壳环境，因此"58 全绿"的证据是缓存记录，未能独立复现。这正是下一节工程化动作要解决的问题。

新增测试中质量较高的：`test_mcp_tools.py`（RPC 握手、降级、双失败兜底）、`conftest.py`（isolated_db / sample_corpus 等 5 个 fixtures，测试间 SQLite 隔离）。

## 4. 新发现：CI 写了，但项目不是 git 仓库

目录中不存在 `.git`。由此产生的连锁事实：

- `.github/workflows/ci.yml`（内容合格：push/PR 触发、3.10/3.11 双矩阵、安装依赖 → 模块导入核验 → pytest 覆盖率门禁 → 离线 benchmark 门禁 → e2e 七步核验）**从未执行过一次**
- "项目有 CI、测试全绿"目前不可对外验证。README 尚未宣称 CI 徽章，没有夸大，但也没有证据
- 环境不可复现问题（V4 P1-3 中实测踩到的"干净环境跑不起来"）依然存在

## 5. 更新后的岗位竞争力结论

| 场景 | V4 评估时 | 本轮评估 |
|---|---|---|
| 字节/阿里校招 Agent 开发岗 | 够用且有竞争力 | 更强。三个塌方追问点（引用线上怎么生效、负迁移怎么测、修订怎么重验证）全部有真代码支撑，可以主动引导面试官进入这些话题 |
| 社招初中级（1-3 年）LLM 应用工程 | 够用，撑两轮深挖 | 够用，能撑第三轮。差分回归、晋级门禁、成本预算均有实现细节可讲 |
| 腾讯 IEG 类 5 年+ 社招 | 不够 | 仍不够。差距在 0→1 生产交付、高并发、多租户、前端全栈，属于项目定位问题，不是短期补代码能解决的 |
| 阿里 Agent Infra 岗 | 方向错配 | 仍错配，不投 |

## 6. 下一步行动（按收益排序）

1. **git init → 核对 .gitignore → 推送 GitHub → 让 CI 真实跑一次**。半小时工作量，收益超过剩余所有代码改进之和：徽章变绿之后，"58 测试 + benchmark 门禁 + e2e 核验全绿"从宣称变成可验证事实。当前 `.gitignore` 仅 175 字节，推送前需补入 `data/*.db`、`.coverage`、`.pytest_cache/`、`research_history/`、`.venv/`。CI 首跑大概率会暴露 requirements.txt 缺 pydantic 声明的问题——这恰好是环境复现性的试金石
2. **MCP 适配器接线**（约 1 小时）：在 Research Agent 的 tools 构造处调用 `create_mcp_tool_adapter`，MCP 失败降级到 Firecrawl 直连。接线之前，简历上只能写"MCP 客户端适配层与降级测试"，不能写"MCP 集成"
3. **pyproject.toml + markers 标注**（半天）：补齐 P1-3 剩余项，顺手收紧差分回归相对退化阈值至 0.02
4. **verdict diff 面板**（半天）：revision 记录增加前后 verdict 变化字段，Streamlit Revision History 渲染。面试演示时这是"反馈闭环"叙事的收尾一屏
5. `build_corpus_from_sources` 死导入清理与 V4 第 36 行笔误订正（5 分钟）

## 7. 面试叙事边界（V4 后修订版）

可以主动展开讲的：corpus 封闭集合的全链路校验（搜索入池 → 状态计算 → 验证 → 修订重算全程传 corpus）；真实双分支差分回归与晋级门禁（baseline 空 memory vs candidate 注入规则，逐 case 比对）；12 条对抗用例覆盖 10 种故障注入；58 个测试与三维预算强制（token/调用数/美元）；记忆隔离区 → 门禁晋级 → 生产注入的完整自进化闭环。

需要主动交代的边界：差分回归中"规则对输出的影响"由 mock 参数化模拟，非真实模型行为仿真；stance 判定是规则启发式，不是语义模型；MCP 是客户端适配层与降级测试，未接入主工作流（接线后此条删除）；单机单用户，分布式仅有设计文档（docs/SYSTEM_DESIGN.md）；CI 配置就绪、待仓库推送后生效。

三个高频追问的答法：

1. 引用校验线上怎么生效——corpus 是封闭集合，每次搜索的文档先入池，所有状态计算与确定性验证都显式传 corpus，引用 URL 不在池内一律 unverified，可现场画数据流。
2. 负迁移怎么测——双分支真实运行 mock workflow，baseline 空 memory、candidate 注入规则，走同一套 parser 与状态机，门禁含绝对阈值与负迁移率阈值；同时说明 mock 仿真规则的诚实边界。
3. 为什么不用 LangGraph——编排是串行四阶段 + 共享 corpus，不需要图状态机；Agents SDK 的 handoff 与工具注册够用且更轻。

## 8. 一句话总结

这轮改进是实打实的：三条断链焊上了两条半，剩余的 P2 项属于锦上添花。当前最大的缺口不在代码质量，而在工程见证——项目还没有被 git 历史和一次绿色的 CI 运行记录过。完成第 6 节第 1 项之后，这个项目的面试故事就完整了。
