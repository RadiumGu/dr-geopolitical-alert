# 代码审阅报告 — DR Geopolitical Alert System

> 审阅日期：2026-03-24
> 审阅范围：PRD v0.1、TDD v0.1、全部 src/ + infra/ + tests/ 代码
> 审阅者：架构审阅猫 🔍（AWS PSA + 客户 PA 双视角）

---

## 【AWS PSA 视角】

### ⚠️ P0 — CloudWatch Dashboard 与 GPRI Engine 指标完全对不上

`dashboard.py` 使用的 namespace 是 `"DrGeopoliticalAlert"`，metrics 名叫 `"GpriScore"`，dimension 用国家代码 `["JP", "CN", "KR", "TW", "US", "RU", "EU"]`。

但 `gpri_calculator.py` 实际发送的是 namespace `"DrAlert/GPRI"`，metric name `"Score"`，dimension 用 AWS Region code（如 `me-central-1`）。

**结果：Dashboard 部署后一条线都看不到。** 而且 `"CN"` `"TW"` `"RU"` `"EU"` 根本不是系统里的任何 Region。

### ⚠️ P0 — Slack Webhook URL 明文泄露进 CloudFormation 模板

`notification.py` 用 `ssm.StringParameter.value_for_string_parameter()` 获取 Slack webhook URL，这会在 **CDK synth 时解析**并将值**明文写入 CloudFormation template JSON**（包括 `cdk.out/` 下的文件）。

应该改为：Lambda 运行时通过 `boto3 ssm.get_parameter(WithDecryption=True)` 读取，或使用 `Secret` + dynamic reference `{{resolve:ssm-secure:...}}`。当前方式等于把 secret 提交到了代码产物里。

### ⚠️ P1 — GPRI Engine 每次运行做 238 次 DynamoDB Query

`get_latest_signals()` 对 **每个 Region × 7 个信号类** 各做一次 `Query(Limit=1)`。34 个 Region = **238 次查询，每 5 分钟**。

DynamoDB on-demand 不会 throttle 这个量级，但这是明显的反模式。更好的做法：
- 每个 Region 只做 1 次 Query（`begins_with("SIG#")`），取最新 7 条按 signal_class 分组
- 或者 collectors 写入后同时维护一个 `LATEST#` 记录，engine 直接读 1 条

### P1 — 信号采集器全部串行调外部 API、无并发

- `weather.py` 对 34 个 Region 逐一调 Open-Meteo API = **34 个串行 HTTP 请求**
- `infrastructure.py` 对每根海缆逐一调 GDELT = 串行
- `bgp.py` 对每个国家逐一调 IODA = 串行

Lambda 60s timeout 下，如果某个外部 API 响应慢（比如 GDELT 经常 5-10s），很容易整体超时。应该用 `concurrent.futures.ThreadPoolExecutor` 并发采集。

### P1 — Open-Meteo 日调用量逼近免费额度上限

34 regions × 144 runs/day（每 10min） = **4,896 calls/day**。Open-Meteo 免费限额 10K/day，仅 weather collector 就用了近 50%。如果加上 retry，很容易超限。

建议：用 Open-Meteo 的 **batch 坐标查询**（一次传多个 `latitude` / `longitude`），可以一次 API call 获取所有 Region 数据。

### P1 — 没有 DLQ（Dead Letter Queue）

9 个 Lambda 都没有配置 DLQ。EventBridge Scheduler 默认 retry 2 次后丢弃。如果某个 collector 连续失败，**信号会静默丢失**，GPRI Engine 会用旧数据算分，没有告警。

CDK 里应给每个 Lambda 加 `dead_letter_queue` 或 `on_failure` destination，并对 DLQ depth 设 CloudWatch Alarm。

### P2 — stubs.py 与正式实现类名冲突

`infra/constructs_/stubs.py` 定义了 `CollectorsConstruct`、`GpriEngineConstruct`、`NotificationConstruct`、`DashboardConstruct`，和实际文件完全重名。虽然 `alert_stack.py` 直接 import 具体文件不会冲突，但这个文件留在目录里是隐患——如果有人不小心 import 到 stubs 就全废了。应该删除或重命名。

### P2 — TDD 声称部署 Region 是 us-west-2，又说"主 Region ap-northeast-1"

TDD 第 1 节："目标账号 926093770964，主 Region ap-northeast-1"。但第 2 节和 CDK `app.py` 都明确是 `us-west-2`。这个矛盾需要澄清——ap-northeast-1 应该是指被监控的"关注 Region"，不是部署 Region，但文档容易误导。

### P2 — TDD 列出但代码中不存在的文件

以下文件在 TDD "项目结构" 和 "测试策略" 里列出，但实际未实现：

- `src/engine/adjudication.py`（综合研判引擎）
- `src/probes/region_probe.py`（跨 Region 技术探针）
- `tests/unit/test_collectors.py`
- `tests/unit/test_adjudication.py`
- `tests/integration/test_e2e.py`

Phase 1 如果没有 adjudication，GPRI × 技术信号矩阵的研判就缺失了。

---

## 【客户 PA 视角】

### ⚠️ P0 — D 类信号（物理基础设施）实现质量极低

PRD 明确说复用 WorldMonitor 的 `get_cable_health` RPC（直接返回海缆健康评分 + 证据链），但实际 `infrastructure.py` 的实现是：**用 GDELT 新闻搜索 "submarine cable cut" 关键词**。

这个实现有严重问题：
1. GDELT 新闻延迟通常 15-60 分钟，远不如实时监控
2. 新闻关键词匹配误报率极高（历史文章、分析类文章都会命中）
3. 没有海缆真正的 status/degraded/fault 状态
4. 很多海缆中断事件新闻报道会晚 12-24 小时

**作为架构决策者，如果 D 类信号是这个质量，那这个维度不如直接标 0，等真正接入 TeleGeography 或 WorldMonitor 再启用。** 虚假的信号比没有信号更危险。

### P1 — B 类信号（网络安全）只看国家级统计，没有 AWS 相关性

`cyber.py` 从 abuse.ch 统计 **某国 IP 地址** 出现在恶意软件 C2/URLhaus 里的数量。但一个国家的"恶意 IP 总数"和"该国 AWS Region 面临的网络威胁"之间关联性很弱。

美国（US）在 Feodo/URLhaus 里永远是数量最多的，但 us-east-1 不一定因此面临更高风险。这个信号噪音太大。

PRD 提到要关注"目标 Region 国家 IP 段的威胁密度**变化**"，但代码只看绝对数量，不看趋势变化。

### P1 — 没有综合研判引擎，GPRI 只是简单加法

PRD 第 5.3 节定义了一个「GPRI × 技术信号 → 综合置信度」的研判矩阵，强调"单一信号不触发高级别告警，需至少两类信号关联确认"。但代码里完全没有这个逻辑。

当前实现是纯加法：`baseline + A + B + C + D + E + F + G`。如果某一天 ACLED 有一批历史数据补录，A 类突然飙到 15，就可能单凭这一个维度把 GPRI 推到 YELLOW/ORANGE，违反了"多信号交叉验证"的设计原则。

### P1 — 合规采集器（F 类）只实现了 OFAC，缺 EU 制裁

PRD 明确说 F 类要监控 OFAC + EU 制裁公报。`compliance.py` 只接了 OFAC RSS，EU 制裁完全没有。对于欧洲 Region（eu-central-1、eu-west-1 等），EU 制裁的影响可能比 OFAC 更直接。

### P2 — 异常倍数计算 vs 绝对值的不一致

不同 collector 的评分逻辑风格差异大：
- `conflict.py` 用的是**相对异常倍数**（7 天 vs 90 天均值）—— 好的设计
- `cyber.py` 用的是**绝对数量**（威胁数 ≥ 50 就高分）—— 没有基线对比
- `bgp.py` IODA 用的是**短期 drop 百分比**（2 小时内）—— 合理但窗口太短

这导致各维度的灵敏度完全不可比。A 类需要异常才报，B 类只要国家大就永远高分（美国永远 ≥ 50 threats）。需要统一为基线对比模式。

### P2 — 测试覆盖严重不足

只有 2 个测试文件：`test_gpri_calculator.py`（GPRI 计算逻辑）和 `test_weather_scoring.py`（天气评分函数）。

7 个 collector **零测试**。这些 collector 都在解析外部 API 响应、做字符串匹配、做数学计算——正是最容易出 bug 的地方。`compliance.py` 里那个 `any(iso2 in _COUNTRY_KEYWORDS for _ in [None])` 就是一个明显的代码异味（虽然功能碰巧正确）。

---

## 【共同关注点】

1. **Dashboard 完全不工作** — namespace、metric name、dimension 全部对不上，部署上去是空白
2. **D 类信号实现是假的** — 用新闻搜索冒充海缆监控，误报/漏报都会很严重
3. **Secret 明文暴露** — Slack webhook 会被写入 CF template
4. **缺乏信号可靠性保障** — 无 DLQ、无采集健康告警，信号静默丢失无人知
5. **代码与设计文档脱节** — adjudication、probes、EU 制裁、多信号交叉验证都只在文档里

---

## 【行动建议】（按优先级）

| 优先级 | 项目 | 工作量 |
|--------|------|--------|
| **P0** | 修复 Dashboard namespace/metric/dimension 使其与 gpri_calculator 对齐 | 0.5h |
| **P0** | Slack webhook 改为 Lambda 运行时从 SSM 读取，不在 CDK synth 时解析 | 1h |
| **P0** | D 类信号暂时禁用或标注为 experimental，不参与 GPRI 计算 | 0.5h |
| **P1** | 给所有 Lambda 加 DLQ + Alarm | 2h |
| **P1** | `get_latest_signals` 优化为单次 Query per Region | 1h |
| **P1** | 采集器加并发（ThreadPoolExecutor）+ Open-Meteo batch 查询 | 3h |
| **P1** | B 类信号改为趋势对比模式，不用绝对数量 | 2h |
| **P1** | 补全 7 个 collector 的单元测试 | 4h |
| **P2** | 删除 stubs.py | 5min |
| **P2** | 补实现 EU 制裁 RSS | 2h |
| **P2** | 实现 adjudication.py（多信号交叉验证） | 4h |
| **P2** | TDD "主 Region" 措辞修正 | 10min |
