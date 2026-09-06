# Deep Research Agent 改进任务书

## 1. 文档目的

本文件用于指导其他大模型或工程师改进当前项目。目标不是简单增加功能，而是把项目从“功能完整的单机 Agent Demo”提升为“具有可信评测、可追溯证据、可治理记忆和基本生产工程能力的 Agent 应用原型”。

改动必须以代码和测试为准，不得仅修改 README、设计文档或 UI 文案来制造“生产级”假象。

## 2. 当前项目定位

当前项目是一个基于 Streamlit、OpenAI Agents SDK、Firecrawl 和 SQLite 的深度研究 Agent，包含：

- Research Agent、Elaboration Agent、Verification Agent 三阶段工作流；
- Firecrawl 深度检索及搜索降级路径；
- Claim-Evidence 结构化证据账本；
- 用户反馈路由和研究历史；
- 记忆隔离区与生产记忆；
- 离线 Benchmark 入口；
- SQLite WAL 持久化。

当前更适合作为面试作品集或研究型原型，不应直接宣称为生产级系统。

## 3. 必须遵守的总原则

1. 先修安全问题，再修数据和评测正确性，最后做架构重构和体验优化。
2. 任何质量指标必须来自真实 Agent 输出或真实模块行为，不得由测试代码手工构造“正确结果”后再自测。
3. 任何 claim 只有在存在可审计证据时才能标记为 `corroborated`。
4. 搜索到网页不等于证据支持；必须区分支持、反驳、中立和未知。
5. 用户反馈、网页内容和模型反思都属于不可信输入，不能直接进入生产记忆或系统指令。
6. 修改报告时，报告、证据账本、审计结果和版本记录必须保持一致。
7. 所有新增逻辑必须有单元测试；关键流程必须有 mock 端到端测试；禁止要求真实 API Key 才能运行测试。
8. 不要为了让测试通过而放宽校验、伪造 URL、自动提升置信度或吞掉异常。

## 4. P0：立即修复的安全问题

### 4.1 清理明文密钥

检查 `config.json` 是否包含真实或疑似真实的 LLM、Firecrawl 等 API Key。

要求：

- 立即删除仓库工作区中的明文密钥；
- 如果密钥曾经有效，提示用户轮换或撤销，不要在输出中打印完整密钥；
- 将凭据改为环境变量或 Streamlit secrets；
- 增加 `.env.example`，只保留变量名和示例占位符；
- 将本地配置文件加入 `.gitignore`；
- `config_manager.py` 不得把原始密钥持久化到普通 JSON；
- 增加启动时的 secret 检测或至少增加测试，防止密钥再次提交。

验收标准：

- 仓库中不存在真实格式的 API Key；
- README 给出安全配置方式；
- 无 Key 时，应用能够启动并给出清晰提示；
- 测试使用假 Key，不依赖外部服务。

## 5. P0：重写真实离线 Benchmark

当前 `benchmark_runner.py` 的主要问题是：它直接构造了正确的 `Claim` 和样本文本，没有调用真实 Agent pipeline，因此输出的 Precision、Grounding 和 Gap Recall 不能证明系统质量。

### 5.1 Benchmark 数据设计

将测试集与评测逻辑分离，例如：

```text
evaluation/
  cases.jsonl
  metrics.py
  runner.py
  reports/
```

每个 case 至少包含：

- `case_id`；
- 研究问题；
- 事实 ground truth；
- 必须包含的关键事实；
- 禁止出现的错误事实；
- 期望的时间边界；
- 期望的来源类型；
- 预置检索结果或 mock 工具响应；
- 注入陷阱或冲突证据；
- 版本和数据更新时间。

### 5.2 Benchmark 执行要求

Benchmark 必须：

1. 使用 mock LLM 和 mock Firecrawl，保证离线、可重复、无需真实 API；
2. 调用真实 pipeline 的核心函数，而不是手工构造最终 ledger；
3. 保存原始模型输出、工具调用记录、中间 ledger 和最终结果；
4. 对每个 claim 做结构化评测；
5. 输出失败原因，而不是只输出一个平均分；
6. 支持基线版本与候选版本对比；
7. 在生产记忆晋级前执行全量回归。

### 5.3 必须实现的指标

- Claim precision：事实是否满足 ground truth 和时间边界；
- Claim recall：关键事实是否被覆盖；
- Citation validity：引用 URL 是否真实存在于检索结果；
- Citation entailment：证据内容是否支持 claim；
- Source quality：是否优先使用一手来源；
- Gap detection recall：是否发现未证实、冲突和缺证据 claim；
- False corroboration rate：错误标记为已证实时的比例；
- Negative transfer rate：新记忆导致既有 case 退化的比例；
- Latency、token、搜索次数和估算成本；
- 工具失败率、JSON 解析失败率和重试次数。

### 5.4 评测门禁

建议初始门槛：

- `false_corroboration_rate == 0`；
- `citation_validity >= 0.95`；
- `gap_detection_recall >= 0.95`；
- `claim_precision >= 0.90`；
- `negative_transfer_rate < 0.05`；
- 关键异常和 schema 失败不能被静默吞掉。

门槛可以调整，但必须在代码和文档中明确，不能只在 UI 中展示“ALL PASSED”。

## 6. P0：修复 Evidence Ledger 的可信性问题

### 6.1 禁止伪造证据 URL

当前解析失败时使用类似 `web-search-context.verified` 的占位 URL，这会被错误地视为真实引用。

要求：

- 删除伪 URL fallback；
- 没有真实 URL 时，claim 必须是 `unverified`；
- EvidenceSource 校验 URL 格式；
- 区分 `retrieved_url`、`canonical_url` 和 `source_id`；
- 记录证据正文摘要或 hash，便于审计；
- 保留解析失败原因。

### 6.2 增加字段校验

Pydantic schema 至少应校验：

- `confidence` 必须在 0 到 1 之间；
- `status` 只能取允许值；
- `stance` 只能取 `supports/refutes/neutral/unknown`；
- `source_type` 只能取枚举或受控扩展值；
- `statement` 非空；
- `url` 必须是合法 HTTP(S) URL；
- 时间字段使用明确格式；
- `corroborated` claim 至少有一条有效证据；
- 关键 claim 需要至少一条一手来源或明确说明为什么没有。

### 6.3 建立 claim 状态转换规则

不要让模型或普通代码任意改变状态。建议明确状态机：

```text
extracted -> unverified
unverified -> corroborated  (证据规则满足)
unverified -> disputed      (存在反驳证据)
disputed -> corroborated    (新增证据解决冲突)
disputed -> unverified      (冲突仍未解决)
```

所有状态转换必须记录：操作者、时间、原因、新增证据、验证器版本。

## 7. P0：修复 Gap-Driven Search 逻辑

当前逻辑在补查后直接将 `disputed/unverified` claim 改为 `corroborated`，但没有解析补查结果或验证证据。

要求：

1. 根据具体 gap 生成定向查询，而不是所有问题都使用同一个固定查询；
2. 保存补查查询、结果、来源和时间；
3. 将补查结果重新解析为 EvidenceSource；
4. 判断新证据的支持/反驳立场；
5. 根据状态机重新计算 claim 状态和置信度；
6. 没有解决证据时保持 `unverified` 或 `disputed`；
7. 限制最大补查轮数、搜索数量、token 和成本；
8. 补查失败不得导致 claim 自动升级。

验收测试至少包括：

- 补查找到支持证据时升级；
- 补查找到反驳证据时保持 disputed；
- 补查无结果时保持 unverified；
- 补查接口异常时不改变原状态；
- 冲突证据同时存在时生成 gap。

## 8. P1：让 Verification Agent 成为真正的逐条验证器

当前 Verification Agent 主要接收报告和前 10 条 claim 摘要，然后生成 Markdown 审计文本，机器无法可靠判断它是否逐条核验。

要求：

- 输入完整的 claim-evidence 列表，或分批验证全部 claim；
- 每个 claim 返回结构化验证结果；
- 验证结果包含 `claim_id`、结论、依据证据、冲突、缺口、置信度和验证方法版本；
- Markdown 报告由结构化结果渲染生成，而不是反过来从 Markdown 猜状态；
- Verification Agent 不得仅依据上游模型的文字判断；
- 对关键数字、日期、发布状态和否定性断言进行特殊处理；
- 发生不一致时优先标记为待核验，不要强行给出肯定结论。

## 9. P1：修复反馈修订后的数据一致性

当前反馈流程主要只更新 `final_report` 和反馈历史，可能导致最终报告与旧 Evidence Ledger、旧审计报告不一致。

要求：

- 引入 `report_version` 或 `revision_id`；
- 每次修订保存完整版本，而不是只覆盖最终文本；
- 记录本次修订影响的 claim；
- 如果修改事实、日期、数字或引用，必须重新运行验证流程；
- 同步更新 Evidence Ledger、audit 和最终报告；
- 支持回滚到上一版；
- 导出档案必须包含版本关系和验证状态；
- 追加反馈时使用同一事务写入报告版本、反馈记录和 ledger 变更。

验收标准：加载任意历史版本时，报告、ledger 和审计结果属于同一版本，且可以说明该版本是否经过验证。

## 10. P1：增强 Prompt Injection 与网页投毒防护

当前防护主要是少量正则匹配，不能覆盖间接提示注入。

要求：

- 将网页内容、用户反馈、工具结果、系统指令和模型输出分层隔离；
- 明确网页内容永远是数据，不是指令；
- 工具返回结构中区分 `content`、`metadata` 和不可信指令文本；
- 对网页中的指令性文本进行标记而不是执行；
- 扩充注入测试集，包括隐藏文本、伪造 system message、链接诱导和多语言攻击；
- 记忆提取前执行安全审查；
- 被标记为危险的候选只能保留在 quarantine，不得人工误操作直接晋级；
- 记录安全扫描版本和命中规则。

不要把正则扫描描述为完整安全防护，应明确它只是第一层过滤器。

## 11. P1：记忆晋级流程必须真正受评测门禁约束

当前人工点击“晋级生产”即可将 quarantine candidate 写入 production memory，Benchmark 结果没有与具体候选绑定。

要求：

- candidate 必须关联 source task、评测版本和评测结果；
- 晋级前自动执行全量 Benchmark；
- 记录 baseline 与 candidate 的指标差异；
- 负迁移超阈值时禁止晋级；
- 支持 rejected、promoted、rolled_back 等状态；
- 生产记忆需要版本号、创建者、审批者、审批时间、适用域和失效时间；
- 支持回滚到上一个稳定版本；
- 生产 Prompt 只加载 active 且已通过门禁的 memory。

## 12. P1：拆分单体文件

当前 `deep_research_openai.py` 同时承担 UI、Agent、工具、工作流、解析、安全、存储调用等职责。

建议拆分为：

```text
app/
  ui/streamlit_app.py
  agents/research_agent.py
  agents/elaboration_agent.py
  agents/verification_agent.py
  workflows/research_workflow.py
  workflows/revision_workflow.py
  tools/firecrawl_tool.py
  evaluation/benchmark_runner.py
  evaluation/metrics.py
  memory/governance.py
  storage/repository.py
  models/schemas.py
  security/input_safety.py
  observability/telemetry.py
```

拆分过程中保持外部功能不变，并逐步增加测试，不要一次性大规模重写导致行为不可比较。

## 13. P1：增加基本生产工程能力

至少补充：

- 外部 API 超时；
- 指数退避重试；
- 最大 Agent 步数；
- 最大工具调用次数；
- token、耗时和成本预算；
- Firecrawl 和 LLM 错误分类；
- 结构化日志；
- request/session trace id；
- 工具调用审计记录；
- 幂等 session 保存；
- 并发写入测试；
- SQLite busy timeout；
- 数据库 schema 迁移版本；
- Docker 或明确的可复现启动方式；
- CI 中运行 lint、type check、unit test、integration test 和 benchmark。

## 14. 测试要求

### 14.1 测试分层

```text
tests/
  unit/
  integration/
  security/
  evaluation/
  fixtures/
```

### 14.2 必须覆盖的场景

- 合法和非法 Claim-Evidence schema；
- 无 JSON、错误 JSON、部分 JSON 和超大 JSON；
- 没有 evidence 的 claim；
- 支持与反驳证据冲突；
- 时间边界冲突；
- 补查成功、失败、超时和空结果；
- Firecrawl 深度接口不可用时的 fallback；
- LLM 请求失败和重试耗尽；
- 反馈修改事实后重新验证；
- 记忆 quarantine、晋级、拒绝和回滚；
- Prompt Injection 样本；
- 多用户或不同 session 之间的数据隔离；
- SQLite 并发和事务回滚；
- 导出和重新加载版本一致性。

### 14.3 测试命令

项目应提供清晰命令，例如：

```bash
python -m unittest discover -v
python -m pytest -q
python -m evaluation.runner --offline
```

如果只支持其中一种测试框架，README 必须明确，不要让项目声明支持 pytest 却没有安装或配置 pytest。

## 15. README 和文档同步要求

完成代码改造后更新 README：

- 准确说明当前系统是原型、单机版还是可部署服务；
- 说明真实 Benchmark 的数据来源、运行方式和限制；
- 说明 API Key 配置方法；
- 说明外部 API、模型和 Firecrawl 是可替换依赖；
- 给出系统架构图和数据流；
- 给出失败处理和安全边界；
- 不得使用“生产级”“完全防注入”“保证事实正确”等无法证明的绝对措辞。

## 16. 推荐实施顺序

### 第一阶段：安全与可信性

1. 清理和轮换密钥；
2. 修复配置管理；
3. 删除伪证据 URL；
4. 增加 schema 校验；
5. 修复补查后的无条件核准；
6. 为上述问题补测试。

### 第二阶段：真实评测与记忆门禁

1. 重写离线 Benchmark；
2. 增加 mock LLM、mock Firecrawl；
3. 增加 claim-level metrics；
4. 将评测结果绑定到 memory candidate；
5. 实现晋级阻断、版本和回滚。

### 第三阶段：版本一致性与验证器

1. 实现逐条 Verification Result；
2. 报告和 ledger 版本化；
3. 反馈修订重新验证；
4. 增加冲突检测和来源评级。

### 第四阶段：工程化重构

1. 拆分单体文件；
2. 增加 timeout、retry、budget 和 tracing；
3. 增加 CI、类型检查和可复现启动；
4. 再考虑服务化、PostgreSQL 和多用户部署。

## 17. 交付要求

改造完成后必须提交：

1. 修改后的源代码；
2. 新增或更新的测试；
3. Benchmark 运行结果，包含每个 case 的失败原因；
4. 安全检查结果；
5. 数据库迁移或兼容说明；
6. README 更新；
7. 一份简短的变更总结，明确哪些问题已解决、哪些仍是限制。

最终报告必须回答：

- 当前系统是否真实调用了 Agent pipeline 进行评测？
- 是否可能把无证据 claim 标记为 corroborated？
- 反馈修订后 ledger 是否与报告一致？
- 生产记忆是否经过可重复的回归门禁？
- 所有测试是否可以在无真实 API Key、无网络条件下运行？
- 当前项目还不能保证什么？

## 18. 给执行模型的工作方式要求

开始修改前，先检查现有代码、测试、依赖和运行命令。每完成一个阶段就运行对应测试，不要等全部改完才验证。

如果发现设计文档与实际代码不一致，以实际代码和可复现测试结果为准，并在变更总结中指出差异。

不要：

- 删除现有功能来规避问题；
- 修改测试断言使测试变得更宽松；
- 用硬编码样例制造 Benchmark 高分；
- 用伪 URL、默认高置信度或自动状态提升掩盖证据不足；
- 把异常全部 `except Exception: pass`；
- 在没有说明的情况下引入真实网络调用到测试；
- 在没有迁移策略的情况下破坏历史数据库；
- 只优化 UI 而不修复核心数据链路。

目标是让项目能够在面试中诚实地展示：Agent 编排、证据工程、评测驱动、记忆治理、安全意识和基本生产工程能力。
