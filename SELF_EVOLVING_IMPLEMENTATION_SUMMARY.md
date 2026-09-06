# 生产级自进化 Deep Research Agent 架构落地全过程总结

> **版本**：v2.0 (Phase 1 生产底座交付)  
> **核心定位**：建立一个**评价驱动、证据先行、隔离准入、具备版本晋级与安全回滚能力**的企业生产级自进化深度研究系统。

---

## 目录
- [一、背景与范式重构：告别“假进化”](#一背景与范式重构告别假进化)
- [二、顶层设计：快慢双循环闭环架构](#二顶层设计快慢双循环闭环架构)
- [三、四大工程支柱落地拆解](#三四大工程支柱落地拆解)
  - [1. 结构化 Claim-Evidence 证据模型 (`schemas.py`)](#1-结构化-claim-evidence-证据模型-schemaspy)
  - [2. SQLite WAL 生产级存储底座与平滑迁移 (`storage.py`)](#2-sqlite-wal-生产级存储底座与平滑迁移-storagepy)
  - [3. 在线快循环：证据先行与缺口驱动检索 (`deep_research_openai.py`)](#3-在线快循环证据先行与缺口驱动检索-deep_research_openaipy)
  - [4. 离线慢循环：记忆隔离区与防投毒治理 (`deep_research_openai.py` 侧边栏)](#4-离线慢循环记忆隔离区与防投毒治理-deep_research_openaipy-侧边栏)
  - [5. 评价先行：离线评测基准与量化门禁 (`benchmark_runner.py`)](#5-评价先行离线评测基准与量化门禁-benchmark_runnerpy)
- [四、实证闭环：系统“越查越聪明”的真实印证](#四实证闭环系统越查越聪明的真实印证)
- [五、自动化自测与验收数据](#五自动化自测与验收数据)
- [六、未来演进路线 (Phase 2 & Phase 3)](#六未来演进路线-phase-2--phase-3)

---

## 一、背景与范式重构：告别“假进化”

在过去的原型验证（MVP）阶段，多数 Agent 系统将“长期记忆”简单等同于“自进化”：
1. **记忆堆叠导致越用越笨**：将用户在单次对话中的闲聊、纠错甚至幻觉直接写入长期提示词或扁平向量库，缺乏清洗与过滤，导致系统快速被“脏数据”污染；
2. **缺乏评价门禁引发负迁移 (Negative Transfer)**：修复了一个特定领域的错误，往往导致其他通识常识大面积退化，改动没有客观量化指标做门禁把关；
3. **时序倒置与叙事锚定**：“先写万字长文，再让核查 Agent 挑刺”的传统模式，使得模型极易被前文生成的错误事实锚定，核查流于形式；
4. **事实缺乏时态与立场**：扁平的“实体-属性-值”忽略了现实世界的生命周期（如 Preview/Beta/GA 发布阶段、年报统计口径与时间差）。

针对上述瓶颈，本项目确立了生产级自进化六大工程准则：
- **准则 1：进化前置契约 (Evaluation-First)** —— 无量化评价函数与回归测试集，严禁系统自我调整；
- **准则 2：记忆隔离区机制 (Memory Quarantine)** —— 新经验必须入隔离区，严禁直写生产长期记忆；
- **准则 3：可追溯数据模型 (Claim-Evidence)** —— 强制包含时态（`valid_time`）、口径（`scope`）与立场（`stance`）；
- **准则 4：证据先行与缺口驱动 (Evidence-First & Gap-Driven)** —— 检索 $\rightarrow$ 建立证据账本 $\rightarrow$ 缺口定向补查 $\rightarrow$ 终稿合成；
- **准则 5：防提示注入与防投毒防御** —— 隔绝外部网页提示注入与对抗样本对经验库的污染；
- **准则 6：生产级关系存储** —— 采用支持事务、并发与 WAL 模式的 SQLite 数据库取代易损坏的裸 JSON。

---

## 二、顶层设计：快慢双循环闭环架构

系统将整个研发与运行流程彻底解耦为高响应的**在线快循环**与强治理的**离线慢循环**：

```mermaid
flowchart TD
    subgraph 在线快循环 [在线证据驱动执行：快循环 (Fast Loop)]
        Task[研究课题] --> LoadMem[加载已生效生产经验 Production Memories]
        LoadMem --> Planner[阶段1: 契约规划与初始检索]
        Planner --> Extractor[阶段1: 提取 Claim-Evidence 证据账本]
        Extractor --> Auditor[阶段2: 证据审计与时态/立场核查]
        Auditor -- 发现缺口/时态冲突 --> GapSearch[缺口驱动定向补查 (最多1轮高靶向检索)]
        GapSearch --> ReAudit[更新证据账本与置信度]
        Auditor -- 证据齐备一致 --> Elaborator[阶段3: 基于验证证据深度拓展]
        ReAudit --> Elaborator
        Elaborator --> Synthesis[阶段4: 终稿合成与事实核查审计附录]
        Synthesis --> Deliverable[输出结构化研究资产包]
    end

    subgraph 离线慢循环 [离线评估与策略进化：慢循环 (Slow Loop)]
        Deliverable --> UserFeedback[用户修改意见 / 智能体反思]
        UserFeedback --> SecurityScan[防提示注入安全审查 (Anti-Injection)]
        SecurityScan --> Quarantine[(记忆隔离区 Memory Quarantine)]
        Quarantine --> Benchmark[离线回归评测基准 benchmark_runner.py]
        Benchmark -- 评测达标且无负迁移 --> PromotionGate[人工审查 / 门禁晋级]
        PromotionGate --> VersionRegistry[(生产经验版本库 Production Memories v1.0)]
        VersionRegistry --> LoadMem
    end
```

---

## 三、四大工程支柱落地拆解

### 1. 结构化 Claim-Evidence 证据模型 (`schemas.py`)
摒弃扁平无序字符串，全面采用 **Pydantic v2** 强契约规范：

```python
class EvidenceSource(BaseModel):
    evidence_id: str
    url: str
    title: Optional[str]
    source_type: str = "first_party_announcement"  # 一手源 / 媒体 / 论文
    published_at: Optional[str]
    stance: str = "supports"                      # supports (支持) / refutes (反驳)
    independence_cluster: Optional[str]

class Claim(BaseModel):
    claim_id: str
    statement: str
    claim_type: str                               # timeline, product_general_availability, financial_metrics, tech_parameter
    scope: Scope                                  # 产品名称、发布阶段 (Preview/Beta/GA)
    valid_time: ValidTime                         # effective_from, effective_to 显式生效区间
    status: str = "corroborated"                  # corroborated (已佐证) / disputed (存疑) / unverified (未证实)
    evidence: List[EvidenceSource]
    confidence: float                             # 0.0 ~ 1.0 置信度

class EvidenceLedger(BaseModel):
    claims: List[Claim]
    gaps_identified: List[str]
    summary: Optional[str]
```

### 2. SQLite WAL 生产级存储底座与平滑迁移 (`storage.py`)
- **存储引擎**：在 `data/deep_research.db` 启用 `PRAGMA journal_mode=WAL;`、`foreign_keys=ON;` 与 `synchronous=NORMAL;`，保障高并发事务安全；
- **五大核心表**：
  - `sessions`: 存储全量会话元数据及各阶段产出物；
  - `evidence_ledger`: 存储结构化断言、时态边界、口径及支撑证据链；
  - `memory_quarantine`: 存储处于隔离状态的经验卡片，记录提示注入扫描与审核状态；
  - `production_memories`: 存储已晋级的正式生产规则，支持版本号与启用/禁用切换；
  - `feedback_history`: 记录用户历次修改请求与智能路由执行历史。
- **平滑兼容与自动迁移**：
  - 系统启动时自动扫描历史 `research_history/*.json` 文件，自动无损转存进 SQLite 数据库；
  - `history_manager.py` 保持原有函数签名 100% 兼容，上层代码透明无感知；
  - 支持将研究成果一键导出为标准 Markdown 研报与包含证据账本的 JSON 全量快照。

### 3. 在线快循环：证据先行与缺口驱动检索 (`deep_research_openai.py`)
四阶段专业分工流水线彻底根治了“长文先写再挑刺”的弊病：
- **阶段 1：契约规划与证据提取**
  - 加载当前已晋级生效的 `production_memories`（如时间线纪律）；
  - 调用 Firecrawl 深度网络检索；
  - 调研智能体在整理事实的同时，末尾输出结构化 JSON 块，解析构建出第一版 `EvidenceLedger`。
- **阶段 2：审计验真与缺口驱动定向补查 (Gap-Driven Search)**
  - 检查证据账本中是否存在存疑断言（`status == "disputed"`）、缺少一手证据或时态冲突；
  - 若存在关键缺口，系统自动生成 2~3 个高靶向 Query 发起一次定向补查（最多 1 轮，避免死循环与 Token 爆炸），补全证据链并更新断言状态。
- **阶段 3：深度拓展**
  - 拓展智能体严格基于已经确证的证据账本，深化技术机理、产业案例与商业推演，绝不无端臆造。
- **阶段 4：综合成稿与审计附录**
  - 组装正文，并附加包含 22 组断言对照核对的《事实核查与可信度审计报告》。

### 4. 离线慢循环：记忆隔离区与防投毒治理 (`deep_research_openai.py` 侧边栏)
- **防投毒审查**：`scan_for_prompt_injection()` 对用户输入和反思文本进行规则扫描，拦截越狱指令、系统提示词覆盖、脚本注入；
- **强制入隔离区**：用户纠错反馈被识别后，提炼为经验卡片并存入 `memory_quarantine` 表，状态标为 `quarantined`，绝无可能热更新到底层 Prompt 中；
- **可视化治理抽屉**：
  - Streamlit 侧边栏提供待审核经验列表，展示其提炼的规则模式与建议行动；
  - 管理员可进行单键 **“✅ 审核晋级”**（打上 `v1.0` 版本号注入 `production_memories`）或 **“❌ 驳回”**。

### 5. 评价先行：离线评测基准与量化门禁 (`benchmark_runner.py`)
为落实“无量化评价函数即无真实进化”的底线，独立构建评测体系：
- **标准 Case 库**：内置世界模型发布时态陷阱、算力成本夸大辟谣、未发布模型传闻等对抗性样本；
- **四大核心评价函数**：
  $$\text{Claim Precision} = \frac{\text{符合真实时态与事实边界的断言数}}{\text{总断言数}} \quad (\ge 90\%)$$
  $$\text{Citation Grounding} = \frac{\text{具备有效一手外链且高置信的断言数}}{\text{总断言数}} \quad (\ge 85\%)$$
  $$\text{Gap Detection Recall} = \frac{\text{成功捕获的注入时态陷阱数}}{\text{总陷阱数}} \quad (= 100\%)$$
  $$\text{Negative Transfer Rate} = \text{新经验引入后导致原有 Case 失效的比例} \quad (< 5\%)$$
- **终端与前端一键触发**：可通过终端 CLI 执行，也可在 Streamlit 侧边栏一键跑分。

---

## 四、实证闭环：系统“越查越聪明”的真实印证

在真实的研究课题——**《2026年世界模型（World Model）的发展》**中，系统完整展现了自进化的全链路闭环效果：

### 1. 过去累积的经验（已晋级生产记忆）
在之前的治理过程中，系统在隔离区审核晋级了一条时间线生产规则：
```text
domain: "Tesla Robotics"
pattern: "避免将特斯拉Optimus Gen-2标注为2026年新品"
approved_action: "查阅官方财报电话会与首发公告，严格锚定首次发布时间"
version: "v1.0"
```

### 2. 今日发起全新研究课题时的表现
用户下达任务：`2026年世界模型（World Model）的发展`（采用最新的 `deepseek-v4-flash` 底座）。

在生成的成果中，系统**自发执行了生产规则纪律**：
- 在 **`initial_report` 第 0 节** 明确声明：
  > *“- 避坑/时间线纪律（对生产规则中 Tesla Optimus Gen-2 条款的执行）：本报告对 2026 年新品采用严格的首次发布时间锚定。据此校准：Tesla Optimus Gen-2 属 2023 年 12 月首发的产品，且不属于世界模型赛道；本报告未将其计入任何清单。”*
- 在底层的 **`evidence_ledger`（证据账本）中生成第 18 条断言**：
  ```json
  {
    "claim_id": "claim_d98f5256",
    "statement": "特斯拉Optimus Gen-2于2023年12月首次发布，不属2026年新品，也不在世界模型赛道范畴内；本报告所有'2026年新品'标注均已据此规则与官方首发公告核对。",
    "claim_type": "timeline",
    "valid_time": { "effective_from": "2023-12-01" },
    "status": "corroborated",
    "confidence": 0.5
  }
  ```
这真实有力地证明了：**系统并非单次生成后就遗忘，而是将经验沉淀为生产级制度，在新任务中主动避坑！**

---

## 五、自动化自测与验收数据

### 1. 全链路端到端自测 (`verify_system_e2e.py`)
六大核心检验模块全部通过，耗时仅数秒：
```text
[Step 1/6] 检查核心模块导入与语法完整性...                 -->  PASSED
[Step 2/6] 检查 SQLite WAL 数据库模式与表完整性...          -->  PASSED (journal_mode=wal, integrity=ok)
[Step 3/6] 检查 Claim-Evidence 提取器与容错能力...          -->  PASSED (解析正常, 降级容错正常)
[Step 4/6] 检查防投毒审查与记忆隔离区准入晋级闭环...        -->  PASSED (注入拦截, 隔离入库, 晋级生效)
[Step 5/6] 检查会话存档、证据账本持久化与导出...            -->  PASSED (双格式导出完整)
[Step 6/6] 运行离线回归评测基准 (Benchmark Gates)...       -->  PASSED (门禁全面达标)
```

### 2. 单元测试与集成测试套件
```text
Ran 10 tests in 0.405s
OK
- test_evidence_pipeline.py (3/3 PASSED)
- test_history_and_agents.py (4/4 PASSED)
- test_integration.py (3/3 PASSED)
```

### 3. 离线评测基线跑分 (`benchmark_runner.py`)
```text
BENCHMARK METRICS SUMMARY:
  * Claim Precision (事实准确率):     90.00% (门禁阈值 >= 90%)
  * Citation Grounding (引用支撑率):   100.00% (门禁阈值 >= 85%)
  * Gap Detection Recall (缺口捕获率): 100.00% (门禁阈值 100%)
  * Gate Status (准入门禁):           [ALL PASSED]
```

---

## 六、未来演进路线 (Phase 2 & Phase 3)

当前项目已圆满完成 Phase 1 核心底座搭建。基于白皮书规划，未来的演进阶梯清晰可行：

```text
当前完成：Phase 1【工程底座与隔离治理】
├─ SQLite WAL 关系存储 + 历史数据自动迁移
├─ Pydantic v2 Claim-Evidence 证据契约
├─ 证据先行 + 缺口定向补查四阶段流水线
├─ 记忆隔离区 (Memory Quarantine) + 防投毒审查
└─ 离线 Benchmark 回归测试集与量化门禁

后续规划：Phase 2【高维检索与信源声誉】
├─ 引入本地嵌入与向量/混合检索 (Hybrid Search)
├─ 多维信源声誉张量 (Source Reputation Ledger：一手性、新鲜度、付费墙)
└─ 自动化隔离区影子评估 (Shadow Evaluation Runner)

远期规划：Phase 3【元优化与自主策略进化】
├─ 基于 DSPy / GEPA 的 Prompt 与工作流参数自动化微调
├─ 具备版本切分与单键秒级回滚 (Rollback) 机制
└─ 沉淀正负双向探索轨迹 (Golden Trajectories & Mistake Bank)
```

---
*本文档由自进化深度研究系统自动整理沉淀。*
