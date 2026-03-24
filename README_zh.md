[English](README.md) | [中文](README_zh.md)

# DR 地缘政治预警系统

> **AWS 跨 Region 容灾预警系统 — 地缘政治风险增强版**

一个全 Serverless 系统，持续监控 **34 个 AWS 商业 Region**，覆盖 7 个风险维度，计算地缘政治风险指数（GPRI, 0–100），在风险等级变化时触发告警 —— 让你在**技术故障发生之前**就能做出容灾决策。

## 为什么需要？

传统 DR 监控只能在故障发生后才检测到。本系统增加了一个**预测层**，通过跟踪地缘政治、环境和网络信号来预判 AWS Region 中断：

- 🌊 巴林附近海缆被切断 → GPRI 在延迟升高**前数小时**就开始上升
- 🌪️ 台风逼近东京 → GPRI 在潜在 AZ 故障**前数天**就发出预警
- 🔒 对某国实施新制裁 → GPRI 标记该 Region 的合规风险

## 架构

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  第 0 层     │     │  第 1 层     │     │  第 2 层     │     │  第 3 层     │
│  信号采集器  │ ──▶ │  GPRI        │ ──▶ │  综合研判    │ ──▶ │  行动触发    │
│  (7 类信号)  │     │  计算引擎    │     │  引擎        │     │  (SNS/Slack) │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
       │                                                              │
       ▼                                                              ▼
  DynamoDB (信号表)                                     CloudWatch 仪表板
  DynamoDB (GPRI表)                                     Slack 通知
```

## 七类信号 (A–G)

| 类别 | 维度 | 权重 | 数据源 | 采集频率 |
|------|------|------|--------|---------|
| **A** | 武装冲突 | 20 | UCDP GED → ACLED 降级 | 10 分钟 |
| **B** | 网络安全威胁 | 15 | abuse.ch (Feodo+URLhaus)，趋势对比 | 10 分钟 |
| **C** | 政治外交稳定性 | 15 | 美国国务院旅行预警 RSS | 10 分钟 |
| **D** | 物理基础设施 | 10 | RIPE Atlas 探针连接率 | 10 分钟 |
| **E** | 极端天气 | 15 | Open-Meteo（批量）+ USGS + GDACS | 10 分钟 |
| **F** | 合规/法规 | 10 | OFAC RSS + 欧盟官方公报 | 10 分钟 |
| **G** | BGP/骨干网 | 15 | IODA（互联网中断检测） | 10 分钟 |

## GPRI 评分模型

```
GPRI = 基线分 + Σ(信号_i × 权重_i)    上限 100
```

34 个 Region 各有一个**静态基线分**反映固有地缘政治风险。实时信号在基线上叠加。

### 基线分说明

每个 Region 的基线分（0–25）根据所在国家/地区的固有风险预设，评估维度：

| 评估因素 | 说明 |
|---------|------|
| 地缘政治紧张度 | 武装冲突、领土争端、国际制裁 |
| 网络主权风险 | 互联网审查、跨境数据管制 |
| 基础设施脆弱性 | 电网稳定性、海缆依赖度 |
| 自然灾害暴露 | 地震带、台风路径、洪灾风险 |
| 法律合规复杂度 | 数据保护法、出口管制 |

**各 Region 基线分**（从高到低）：

| 基线 | Region |
|------|--------|
| 25 | 🇮🇱 il-central-1 (Tel Aviv) |
| 20 | 🇦🇪 me-central-1 (Dubai) |
| 18 | 🇧🇭 me-south-1 (Bahrain) |
| 15 | 🇿🇦 af-south-1 (Cape Town) |
| 12 | 🇭🇰 ap-east-1 (Hong Kong) |
| 10 | 🇰🇷 ap-east-2, 🇮🇳 ap-south-1/2, 🇮🇩 ap-southeast-3, 🇧🇷 sa-east-1, 🇲🇽 mx-central-1 |
| 8–9 | 🇹🇭 ap-southeast-6, 🇦🇺 ap-southeast-4, 🇳🇿 ap-southeast-5, 🇲🇾 ap-southeast-7, 🇮🇹 eu-south-1, 🇪🇸 eu-south-2 |
| 5–6 | 🇯🇵 ap-northeast-1/3, 🇰🇷 ap-northeast-2 |
| 2–4 | 🇺🇸 us-east-1/2, us-west-1/2, 🇨🇦 ca-central-1, ca-west-1, 🇩🇪 eu-central-1, 🇬🇧 eu-west-2, 🇫🇷 eu-west-3, 🇸🇪 eu-north-1, 🇦🇺 ap-southeast-2, 🇨🇭 eu-central-2, 🇮🇪 eu-west-1, 🇸🇬 ap-southeast-1 |

### 风险等级

| 等级 | 范围 | 颜色 | 建议动作 |
|------|------|------|---------|
| GREEN | 0–30 | 🟢 | 正常运营 |
| YELLOW | 31–50 | 🟡 | 加强监控，Review DR 就绪状态 |
| ORANGE | 51–70 | 🟠 | 主动备战：Scale Up 备用 Region，降低 TTL |
| RED | 71–85 | 🔴 | 建议撤离：启动切换决策流程 |
| BLACK | 86–100 | ⚫ | 立即执行 DR 切换 |

### 综合研判（交叉验证）

单一信号主导的告警会被**降级**（低置信度）。多个信号关联确认的告警会被**升级**（高置信度）。防止单一数据源噪音导致误报。

## 部署

### 前置条件

- AWS 账号，us-west-2 已完成 CDK bootstrap
- Python 3.12+
- AWS CDK CLI

### 一键部署

```bash
cd dr-geopolitical-alert
pip install -r requirements.txt

# 部署（使用默认 AWS 账号，Region 默认 us-west-2）
cdk deploy

# 或指定其他 Region
CDK_DEPLOY_REGION=eu-west-1 cdk deploy
```

系统部署在 **us-west-2（Oregon）** —— 独立于被监控 Region，确保控制平面可用性。

### 部署后配置

```bash
# 设置 Slack webhook 接收告警通知
aws ssm put-parameter \
  --name "/dr-alert/slack-webhook-url" \
  --value "https://hooks.slack.com/services/你的/WEBHOOK/URL" \
  --type String --region us-west-2 --overwrite

# 手动触发验证
aws lambda invoke --function-name dr-alert-collector-weather --region us-west-2 /tmp/out.json
aws lambda invoke --function-name dr-alert-gpri-calculator --region us-west-2 /tmp/out.json
```

部署完成后，CDK 会输出 **GPRI 查询 API URL**（Lambda Function URL），可以直接使用。

## GPRI 查询 API

公开的只读 API，查询实时 GPRI 评分 —— 无需认证。

### 查询单个 Region

```bash
curl "https://<your-function-url>/?region=il-central-1"
```

```json
{
  "region": "il-central-1",
  "gpri": 42,
  "level": "GREEN",
  "confidence": "LOW",
  "components": {"A": 0, "B": 0, "C": 15, "D": 2, "E": 0, "F": 0, "G": 0},
  "timestamp": "2026-03-24T16:50:22Z"
}
```

### 查询全部 34 个 Region

```bash
curl "https://<your-function-url>/"
```

```json
{
  "count": 34,
  "regions": [
    {"region": "il-central-1", "gpri": 42, "level": "GREEN", "city": "Tel Aviv", "country": "IL", "baseline": 25, ...},
    {"region": "me-central-1", "gpri": 31, "level": "GREEN", "city": "Dubai", "country": "AE", "baseline": 20, ...},
    ...
  ]
}
```

> Function URL 在 `cdk deploy` 输出中显示为 `DrGeopoliticalAlertStack.ApiGpriQueryUrl`。

## AWS 资源清单

| 资源 | 数量 | 用途 |
|------|------|------|
| Lambda 函数 | 10 | 7 采集器 + 1 GPRI 引擎 + 1 Slack 通知 + 1 API 查询 |
| DynamoDB 表 | 2 | `dr-alert-signals` + `dr-alert-gpri` |
| EventBridge 规则 | 8 | 7 × 10分钟（采集器）+ 1 × 5分钟（GPRI） |
| SNS Topic | 1 | GPRI 等级变化告警 |
| SQS 队列 | 1 | 死信队列（失败调用） |
| CloudWatch 仪表板 | 1 | 39 个 widget，覆盖全部 34 Region |
| CloudWatch 告警 | 1 | DLQ 深度 > 0 |

**预估月费：$5–15**（全 Serverless，按用量计费）

### 费用明细

| 资源 | 预估费用 | 说明 |
|------|---------|------|
| Lambda | ~$2–5 | 9 个函数 × 每月约 4,300 次调用（每 5-10 分钟），256MB，平均 <3s |
| DynamoDB | ~$1–3 | On-demand 模式；每个采集器每月约 4,300 次写入 + 每轮 34 次 GPRI 写入 |
| EventBridge | 免费 | 8 条规则，在免费额度内 |
| CloudWatch 仪表板 | $3 | 1 个自定义仪表板 |
| SNS/SQS | ~$0 | 极少使用（仅等级变化时触发） |
| CloudWatch 告警 | ~$0.10 | 1 个告警 |
| **合计** | **约 $6–11/月** | 无 NAT Gateway、无 VPC、无预留容量 |

> 所有外部数据源（UCDP、ACLED、abuse.ch、RIPE Atlas、Open-Meteo、USGS、GDACS、IODA、OFAC、State Dept）均为**免费公开 API**——无需 API Key 或付费订阅。

## 数据源

所有信号采集器使用**免费公开 API**，无需认证（Cloudflare Radar 为未来 D 类增强选项，需 API token）：

| 类别 | 数据源 | API 端点 | 提供内容 |
|------|--------|---------|---------|
| **A** | [UCDP GED](https://ucdp.uu.se/) | `https://ucdpapi.pcr.uu.se/api/gedevents/` | 地理编码武装冲突事件 |
| **A** | [ACLED](https://acleddata.com/)（降级） | `https://api.acleddata.com/acled/read` | 政治暴力与抗议事件 |
| **B** | [abuse.ch Feodo Tracker](https://feodotracker.abuse.ch/) | `https://feodotracker.abuse.ch/downloads/ipblocklist_recommended.txt` | 僵尸网络 C2 IP 黑名单 |
| **B** | [abuse.ch URLhaus](https://urlhaus.abuse.ch/) | `https://urlhaus-api.abuse.ch/v1/urls/recent/` | 恶意软件分发 URL |
| **C** | [美国国务院旅行预警](https://travel.state.gov/) | `https://travel.state.gov/_res/rss/TAsTWs.xml` | 国家旅行风险等级 (1–4) |
| **D** | [RIPE Atlas](https://atlas.ripe.net/) | `https://atlas.ripe.net/api/v2/probes/` | 各国网络探针连接率 |
| **E** | [Open-Meteo](https://open-meteo.com/) | `https://api.open-meteo.com/v1/forecast` | 极端天气预警（批量 API） |
| **E** | [USGS 地震](https://earthquake.usgs.gov/) | `https://earthquake.usgs.gov/.../significant_week.geojson` | 重大地震事件 |
| **E** | [GDACS](https://www.gdacs.org/) | `https://www.gdacs.org/xml/rss.xml` | 全球灾害预警（洪水、气旋、火山） |
| **F** | [OFAC SDN](https://ofac.treasury.gov/) | `https://sanctionssearch.ofac.treas.gov/`（RSS） | 美国制裁名单更新 |
| **F** | [EU Official Journal](https://eur-lex.europa.eu/) | `https://eur-lex.europa.eu/rss/...` | 欧盟法规变更 |
| **G** | [IODA (CAIDA)](https://ioda.inetintelligence.cc/) | `https://api.ioda.inetintelligence.cc/v2/signals/raw/country/` | 互联网中断检测（BGP、主动探测、暗网） |

## 项目结构

```
dr-geopolitical-alert/
├── infra/                   # CDK 基础设施（Python）
│   ├── app.py              # CDK 入口
│   ├── stacks/alert_stack.py
│   └── constructs_/
│       ├── tables.py        # DynamoDB 表定义
│       ├── collectors.py    # 7 Lambda + EventBridge 调度
│       ├── gpri_engine.py   # GPRI 计算 Lambda
│       ├── notification.py  # SNS + Slack Lambda
│       ├── dashboard.py     # CloudWatch 仪表板
│       └── api.py           # GPRI 查询 Lambda Function URL
├── src/                     # Lambda 源码
│   ├── api/
│   │   └── gpri_query.py    # 公开 GPRI 查询端点
│   ├── collectors/          # 7 个信号采集器（A–G 类）
│   ├── engine/
│   │   ├── gpri_calculator.py
│   │   └── adjudication.py  # 多信号交叉验证
│   ├── notify/
│   │   └── slack_dispatcher.py
│   └── shared/
│       ├── types.py         # 数据模型 + 枚举
│       ├── region_config.py # 34 Region 定义 + 基线分
│       ├── db.py            # DynamoDB 操作
│       └── http_client.py   # 韧性 HTTP 客户端
├── tests/unit/              # 100 个单元测试
├── cdk.json
├── requirements.txt
└── conftest.py
```

## 测试

```bash
python3 -m pytest tests/ -v
# 100 passed
```

## 监控

**CloudWatch 仪表板**: us-west-2 的 `DrGeopoliticalAlert`

仪表板包含 39 个 widget，布局如下：

- **标题栏**: `GPRI Total = Baseline (BL) + Real-Time Signals (A-G)` + 颜色级别图例
- **34 个 Region 数值卡片**: 按基线风险从高到低排列，每个显示：
  - 实时 GPRI 总分（基线 + 信号）
  - Sparkline 趋势线
  - 标题含 `BL:xx` 表示该 Region 的静态基线分
  - 分层标识：🔴 高基线(≥15)、🟡 中等(≥10)、🔵 一般(≥6)、🟢 低风险(<6)
- **时间线图**: `GPRI Total Score (Baseline + Signals) — Top 10 Risk Regions`，带 YELLOW/ORANGE/RED/BLACK 阈值线
- **3 个信号分解图**: `Real-Time Signals Only (excl. Baseline)`，展示 Top 3 风险 Region（Israel、Bahrain、Dubai）的 7 维信号堆叠面积图

> **说明**: 上方数值卡片的 GPRI 总分 = 基线 + 信号。下方信号分解图**只显示实时信号部分**，不含静态基线。两者的差值恒等于该 Region 的基线分（标题中的 `BL:xx`）。

## 关键设计决策

| 决策 | 说明 |
|------|------|
| **部署在 us-west-2** | 独立于被监控 Region；基线风险最低；离数据源（ACLED/OFAC/IODA 在北美/欧洲）最近 |
| **不使用 VPC** | Lambda 直接访问公网 API + DynamoDB 端点；冷启动更快，无 NAT Gateway 费用 |
| **综合研判引擎** | 防止单一信号误报（PRD §5.3 "多信号交叉验证"） |
| **D 类用 RIPE Atlas** | 替换不可靠的 GDELT 新闻搜索，使用真实网络遥测数据 |
| **B 类趋势对比** | 为历史基线异常倍数 |

## 免责声明

本项目**仅供学习和参考使用**。GPRI 评分、基线风险评估和信号分析基于公开数据和简化的启发式模型，**不构成**专业的地缘政治、安全或容灾建议。基线分反映的是国家级别的风险概况，可能无法准确代表特定 AWS 数据中心所在地的实际情况。在基于本系统的输出做出关键基础设施或容灾决策前，请务必进行独立的尽职调查并咨询专业人士。作者不对基于 GPRI 评分或告警所采取的行动承担任何责任。

## 许可证

MIT
