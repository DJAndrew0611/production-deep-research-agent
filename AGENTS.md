# 生产级自进化 Agent 架构设计与工程准则 (Production Agent Architecture Rules)

本规则适用于本工作区及相关 Agent 系统的设计、实现、演进与代码审查：

## 1. 进化前置契约 (Evaluation-First Constraint)
- 任何具备“策略自调整、Prompt 自动优化、记忆动态沉淀”的智能体系统，必须配套显式的离线 Benchmark 回归测试集与可量化评价函数（如 LiveResearchBench、事实准确率、负迁移率、Token 成本）。
- 禁止缺乏量化评测门禁的“假进化”或无约束热更新，防止系统因过拟合单次反馈而整体退化。

## 2. 记忆准入与隔离区机制 (Memory Quarantine Principle)
- 任何由模型自主反思（Reflection）、网页爬取或单次用户反馈生成的经验卡片，**严禁直接写入生产长期记忆**。
- 新记忆必须进入隔离区（Quarantine），经离线一致性核验、防投毒测试以及回归集验证达标后，方可晋级为正式生产经验。

## 3. 可追溯数据模型 (Claim-Evidence Principle)
- 事实资产严禁采用无时态的扁平 Key-Value 或简单实体三元组。
- 必须采用结构化 Claim-Evidence 模式，强制显式声明：
  - `claim_type`: 断言类型（如发布状态、财务数据、技术参数）
  - `valid_time`: 生效时段（区分预览、Beta、GA 与统计基准年份）
  - `scope`: 适用范围与限定口径
  - `evidence`: 包含一手性（first-party）、证据链、独立信源聚类与支持/反驳立场（stance）
  - `confidence`: 置信度评分与核验方法版本

## 4. 证据先行与缺口驱动 (Evidence-First & Gap-Driven Search)
- 严禁采用“先写长文、后挑刺核查”的倒置时序（易造成前文错误叙事锚定与正文丢失）。
- 流水线必须坚持：任务契约 -> 多源检索 -> Evidence Ledger 建立 -> 断言级核验与冲突检测 -> **证据不足驱动缺口再检索** -> 证据链完备后最终成稿。

## 5. 安全威胁与防投毒防御 (Prompt Injection & Memory Poisoning Defense)
- 将外部不可信网页文本转化为系统经验/指令的链路，必须实施上下文严格隔离与投毒防御审查。
- 限制反射提取模块的权限范围，防范对抗样本通过网页提示注入污染长期经验库。

## 6. 生产级存储与状态管理
- 运行时存储应采用支持事务、并发写入与 WAL 模式的关系数据库（如 SQLite WAL / PostgreSQL），纯 JSON 文件仅限作为导入导出和单次快照使用。
- 格式与排版控制必须通过 JSON Schema、Pydantic 契约或章节模板强制约束，不得依赖不可靠的大模型 Temperature 控制。
