# Technical Design Document (TDD)

> AWS 跨 Region 容灾预警系统 — 地缘政治风险增强版
> 版本：v0.1 | 日期：2026-03-24

---

## 1. 概述

基于 PRD v0.1 的技术实施方案。全 Serverless 架构，CDK Python 部署，目标账号 YOUR_ACCOUNT_ID。部署 Region: us-west-2（Oregon），用户主要使用（被监控）Region: ap-northeast-1（Tokyo）。部署 Region 独立于被监控 Region，确保控制平面可用性。

---

## 2. 架构总览

```
┌─ EventBridge Scheduler ──────────────────────────────────┐
│  每 10min: 信号采集 (7 个 Lambda)                          │
│  每 5min:  GPRI 计算 (1 个 Lambda)                        │
│  每 1min:  技术探针 (跨 Region Lambda)  [Phase 1 可选]     │
└──────────────────────────┬───────────────────────────────┘
                           │ write
              ┌────────────▼────────────┐
              │      DynamoDB           │
              │  signals  (原始信号 TTL 7d) │
              │  gpri     (评分历史 TTL 90d)│
              └────────────┬────────────┘
                           │ read
              ┌────────────▼────────────┐
              │    GPRI Engine Lambda    │
              │  聚合 → 评分 → 研判      │
              │  级别变化 → publish SNS  │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │  SNS Topic              │
              │  ├── Slack Webhook      │
              │  ├── Email              │
              │  └── (未来: PagerDuty)  │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │  CloudWatch Dashboard    │
              │  GPRI Gauge + 趋势图     │
              └─────────────────────────┘
```

**部署 Region：us-west-2（Oregon）** — 基线风险最低，AWS 服务最全，到北美/欧洲数据源延迟最低，独立于被监控 Region。

---

## 3. 项目结构

```
dr-geopolitical-alert/
├── doc/
│   ├── prd.md                          # 产品需求文档
│   └── tdd.md                          # 本文档
├── infra/                              # CDK Python 项目
│   ├── app.py                          # CDK 入口
│   ├── cdk.json                        # CDK 配置
│   ├── requirements.txt                # Python 依赖
│   ├── stacks/
│   │   └── alert_stack.py              # 主 Stack
│   └── constructs/
│       ├── tables.py                   # DynamoDB 表
│       ├── collectors.py               # 7 类信号采集器 Lambda
│       ├── gpri_engine.py              # GPRI 引擎 Lambda
│       ├── notification.py             # SNS + Slack 通知
│       ├── dashboard.py                # CloudWatch Dashboard
│       └── probes.py                   # 跨 Region 技术探针
├── src/                                # Lambda 函数代码 (Python)
│   ├── collectors/
│   │   ├── conflict.py                 # A类: ACLED + UCDP
│   │   ├── cyber.py                    # B类: abuse.ch
│   │   ├── political.py                # C类: 旅行预警
│   │   ├── infrastructure.py           # D类: 海缆 + 航运
│   │   ├── weather.py                  # E类: Open-Meteo + GDACS + USGS
│   │   ├── compliance.py               # F类: OFAC RSS + EU RSS
│   │   └── bgp.py                      # G类: Cloudflare Radar + IODA
│   ├── engine/
│   │   ├── gpri_calculator.py          # GPRI 评分计算
│   │   └── adjudication.py             # 综合研判
│   ├── notify/
│   │   └── slack_dispatcher.py         # Slack 告警格式化
│   ├── probes/
│   │   └── region_probe.py             # 跨 Region API 探测
│   └── shared/
│       ├── types.py                    # 数据模型
│       ├── db.py                       # DynamoDB 操作封装
│       ├── region_config.py            # 34 Region 静态配置
│       └── http_client.py              # 带重试的 HTTP 客户端
├── tests/
│   ├── unit/
│   │   ├── test_gpri_calculator.py     # GPRI 计算单测
│   │   ├── test_collectors.py          # 采集器单测（mock API）
│   │   └── test_adjudication.py        # 研判逻辑单测
│   └── integration/
│       └── test_e2e.py                 # 端到端测试
└── README.md
```

---

## 4. DynamoDB 表设计

### 4.1 signals 表

存储各类信号采集的原始/标准化数据。

| 字段 | 类型 | 说明 |
|------|------|------|
| PK | String | `REGION#{region_code}` 例 `REGION#me-central-1` |
| SK | String | `SIG#{signal_class}#{iso_timestamp}` 例 `SIG#A#2026-03-24T15:00:00Z` |
| signal_class | String | A/B/C/D/E/F/G |
| score | Number | 该维度得分（0 到维度上限） |
| raw_data | Map | 原始数据摘要（事件数、关键事件等） |
| source | String | 数据源标识 |
| collected_at | String | ISO 8601 采集时间 |
| ttl | Number | Unix timestamp，7 天后过期 |

**GSI-1**：`signal_class-collected_at-index`
- PK: `signal_class`, SK: `collected_at`
- 用途：按信号类型查询最新数据（跨 Region）

**容量模式**：On-Demand（PAY_PER_REQUEST）

### 4.2 gpri 表

存储各 Region 的 GPRI 评分历史。

| 字段 | 类型 | 说明 |
|------|------|------|
| PK | String | `REGION#{region_code}` |
| SK | String | `TS#{iso_timestamp}` |
| gpri | Number | 综合评分 0-100 |
| level | String | GREEN/YELLOW/ORANGE/RED/BLACK |
| prev_level | String | 上次级别（用于检测变化） |
| components | Map | `{ A: 8, B: 3, C: 5, D: 0, E: 5, F: 0, G: 0 }` |
| baseline | Number | 静态基线分 |
| compliance_block | Boolean | F 类触发的合规阻断标志 |
| ttl | Number | Unix timestamp，90 天后过期 |

**GSI-2**：`level-gpri-index`
- PK: `level`, SK: `gpri`
- 用途：快速查询所有 RED/BLACK Region

**容量模式**：On-Demand

---

## 5. Lambda 函数设计

### 5.1 通用配置

| 参数 | 值 |
|------|-----|
| Runtime | Python 3.13 |
| Architecture | arm64 (Graviton) |
| Memory | 256 MB |
| Timeout | 60s（采集器）/ 30s（引擎）/ 15s（通知） |
| 环境变量 | `SIGNALS_TABLE`, `GPRI_TABLE`, `SNS_TOPIC_ARN` |
| Powertools | aws-lambda-powertools[all]（日志/指标/追踪） |

### 5.2 信号采集器

每个采集器的职责：拉取外部 API → 标准化 → 按 Region 计算该维度得分 → 写 DynamoDB。

#### A 类: conflict.py

```python
def handler(event, context):
    # 1. 拉取 ACLED API（近 7 天，目标国家）
    acled_events = fetch_acled(countries=["AE","BH","IL","ZA",...], days=7)
    
    # 2. 拉取 UCDP API（活跃冲突）
    ucdp_events = fetch_ucdp()
    
    # 3. 对每个 Region，按关联国家过滤事件
    for region in ALL_REGIONS:
        country = region.country
        events = [e for e in acled_events if e.country == country]
        
        # 4. 计算异常倍数 = 近7天事件数 / 90天日均
        anomaly_ratio = len(events) / (baseline_90d[country] * 7)
        
        # 5. 评分 (0-20)
        score = min(20, int(anomaly_ratio * 5))
        
        # 6. 写 DynamoDB
        put_signal(region.code, "A", score, raw_data={...})
```

#### B 类: cyber.py

```python
def handler(event, context):
    # abuse.ch Feodo Tracker + URLhaus
    # 按目标国家 IP 段过滤威胁密度
    # 评分 0-15
```

#### C 类: political.py

```python
def handler(event, context):
    # 美国国务院旅行预警页面解析
    # 提取 Level 1-4
    # Level 1→0, Level 2→3, Level 3→8, Level 4→15
```

#### D 类: infrastructure.py

```python
def handler(event, context):
    # WorldMonitor cable_health API 或直接从 TeleGeography
    # 海缆状态: ok→0, degraded→5, fault→10
    # 航运预警（UKMTO RSS）
```

#### E 类: weather.py

```python
def handler(event, context):
    # Open-Meteo API（免费，无需 Key）
    # 每个 Region 的坐标，查 72h 预报
    # 温度 ≥45°C→+8, 降雨 ≥20mm/h→+10, 风速 ≥100km/h→+7
    
    # GDACS RSS 橙/红色灾害
    # USGS Earthquake API（M≥5.0, 100km 内→+15）
    
    # 综合评分 0-15
```

#### F 类: compliance.py

```python
def handler(event, context):
    # OFAC RSS (home.treasury.gov/rss.xml)
    # EU 制裁 RSS (eur-lex.europa.eu)
    # 关键词匹配目标国家
    # 评分 0-10, 同时设置 compliance_block 标志
```

#### G 类: bgp.py

```python
def handler(event, context):
    # Cloudflare Radar API — BGP 路由变更量
    # IODA API — 国家级断网检测
    # AS16509 (AWS) 路由变更 > P99*3 → HIGH
    # 评分 0-15
```

### 5.3 GPRI Engine (gpri_calculator.py)

```python
WEIGHTS = {
    "A": 20,  # 武装冲突
    "B": 15,  # 网络威胁
    "C": 15,  # 政治稳定
    "D": 10,  # 物理基础设施
    "E": 15,  # 极端天气
    "F": 10,  # 合规法规
    "G": 15,  # BGP/骨干网
}

THRESHOLDS = {
    "GREEN":  (0, 30),
    "YELLOW": (31, 50),
    "ORANGE": (51, 70),
    "RED":    (71, 85),
    "BLACK":  (86, 100),
}

def handler(event, context):
    for region in ALL_REGIONS:
        # 1. 读取每类信号最近一次得分
        signals = get_latest_signals(region.code)
        
        # 2. 加权求和
        gpri = region.baseline
        for cls, score in signals.items():
            gpri += score  # score 已在采集时按维度上限计算
        gpri = min(100, gpri)
        
        # 3. 判断级别
        level = get_level(gpri)
        prev = get_previous_level(region.code)
        
        # 4. 写入 gpri 表
        put_gpri(region.code, gpri, level, prev, signals)
        
        # 5. 级别变化 → 发 SNS
        if level != prev:
            publish_alert(region, gpri, level, prev, signals)
        
        # 6. 发 CloudWatch Metric
        put_metric(f"GPRI/{region.code}", gpri)
```

### 5.4 Slack 通知 (slack_dispatcher.py)

```python
def handler(event, context):
    # SNS 消息 → 格式化 Slack Block Kit 消息
    # 🟢🟡🟠🔴⚫ 按级别显示
    # 包含: GPRI 值、变化方向、触发维度、建议动作、Region 信息

    message = {
        "blocks": [
            {"type": "header", "text": f"{emoji} GPRI {level} — {region} ({city})"},
            {"type": "section", "text": f"Score: {gpri}/100 ({direction} from {prev_gpri})"},
            {"type": "section", "text": format_components(components)},
            {"type": "section", "text": f"建议: {recommendation}"},
        ]
    }
    requests.post(SLACK_WEBHOOK_URL, json=message)
```

---

## 6. CDK Stack 设计

### 6.1 主 Stack (alert_stack.py)

```python
class DrGeopoliticalAlertStack(Stack):
    def __init__(self, scope, id, **kwargs):
        super().__init__(scope, id, **kwargs)
        
        # 1. DynamoDB 表
        tables = TablesConstruct(self, "Tables")
        
        # 2. 信号采集器 (7 Lambda + 7 Scheduler)
        collectors = CollectorsConstruct(self, "Collectors",
            signals_table=tables.signals_table)
        
        # 3. GPRI 引擎 (1 Lambda + 1 Scheduler)
        engine = GpriEngineConstruct(self, "Engine",
            signals_table=tables.signals_table,
            gpri_table=tables.gpri_table,
            sns_topic=notification.topic)
        
        # 4. 通知 (SNS + Slack Lambda)
        notification = NotificationConstruct(self, "Notification",
            slack_webhook_url=ssm_param)
        
        # 5. Dashboard
        dashboard = DashboardConstruct(self, "Dashboard",
            gpri_table=tables.gpri_table)
```

### 6.2 Construct 拆分

| Construct | 资源 |
|-----------|------|
| TablesConstruct | 2 DynamoDB 表 + GSI |
| CollectorsConstruct | 7 Lambda + 7 EventBridge Rule + 1 Lambda Layer (shared) |
| GpriEngineConstruct | 1 Lambda + 1 EventBridge Rule |
| NotificationConstruct | 1 SNS Topic + 1 Lambda (Slack) + Email Subscription |
| DashboardConstruct | 1 CloudWatch Dashboard (GPRI gauges + 趋势) |
| ProbesConstruct | N Lambda (跨 Region 部署) — Phase 1 可选 |

### 6.3 Lambda Layer

共享依赖打包为 Lambda Layer：
- `requests`（HTTP 客户端）
- `feedparser`（RSS 解析）
- `aws-lambda-powertools`（日志/指标/追踪）
- `shared/`（db.py, types.py, region_config.py, http_client.py）

---

## 7. 外部 API 接入详情

### 7.1 免费 API（无需注册）

| API | 用途 | 端点 | 限制 |
|-----|------|------|------|
| Open-Meteo | 天气预报 | `api.open-meteo.com/v1/forecast` | 10K req/day |
| USGS Earthquake | 地震 | `earthquake.usgs.gov/fdsnws/event/1/query` | 无限制 |
| GDACS | 灾害预警 | `gdacs.org/gdacsapi/api/events/geteventlist` | 无限制 |
| abuse.ch | 网络威胁 | `feodotracker.abuse.ch/downloads/ipblocklist.json` | 无限制 |
| IODA | 断网检测 | `api.ioda.inetintel.cc.gatech.edu/v2/` | 无限制 |
| UCDP | 冲突数据 | `ucdpapi.pcr.uu.se/api/` | 无限制 |

### 7.2 免费 API（需注册获取 Key）

| API | 用途 | 获取 Key | 限制 |
|-----|------|---------|------|
| ACLED | 武装冲突 | `acleddata.com` 注册 | 免费，需学术/机构邮箱 |
| Cloudflare Radar | BGP + 流量 | `dash.cloudflare.com` 注册 | 免费 Tier |

### 7.3 API Key 存储

所有 API Key 存储在 SSM Parameter Store（SecureString）：

```
/dr-alert/acled-api-key
/dr-alert/cloudflare-radar-token
/dr-alert/slack-webhook-url
```

CDK 中通过 `ssm.StringParameter.from_secure_string_parameter_attributes()` 引用。

---

## 8. CloudWatch Dashboard

### 8.1 布局

```
┌─────────────────────────────────────────────────────────────┐
│  🌐 Global GPRI Overview                                    │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐    │
│  │ 🔴   │ │ 🟠   │ │ 🟡   │ │ 🟢   │ │ 🟢   │ │ 🟢   │    │
│  │ME-C-1│ │AF-S-1│ │AP-E-1│ │US-E-1│ │EU-C-1│ │AP-SE1│    │
│  │ 72   │ │ 55   │ │ 38   │ │ 8    │ │ 12   │ │ 5    │    │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘    │
├─────────────────────────────────────────────────────────────┤
│  📈 GPRI Trend (24h) — Top 5 Risk Regions                  │
│  [折线图: me-central-1, af-south-1, il-central-1, ...]     │
├─────────────────────────────────────────────────────────────┤
│  📊 Signal Breakdown — me-central-1                         │
│  A ████████░░ 12/20   E ██████░░░░ 10/15                   │
│  B ███░░░░░░░  3/15   F ░░░░░░░░░░  0/10                   │
│  C █████░░░░░  5/15   G ████████░░  8/15                    │
│  D ░░░░░░░░░░  0/10                                        │
├─────────────────────────────────────────────────────────────┤
│  🔔 Alert History (7d)                                      │
│  [表格: 时间, Region, 级别变化, GPRI, 触发信号]              │
├─────────────────────────────────────────────────────────────┤
│  ❤️ Collector Health                                        │
│  conflict: ✅ 2min ago  | cyber: ✅ 3min ago                │
│  weather:  ✅ 1min ago  | bgp:   ✅ 4min ago                │
└─────────────────────────────────────────────────────────────┘
```

### 8.2 Custom Metrics

| Namespace | Metric | Dimension | 说明 |
|-----------|--------|-----------|------|
| DrAlert/GPRI | Score | Region | GPRI 评分 |
| DrAlert/GPRI | Level | Region | 级别编号 (0-4) |
| DrAlert/Signal | Score | Region, Class | 各维度得分 |
| DrAlert/Collector | Latency | Collector | 采集器耗时 |
| DrAlert/Collector | Errors | Collector | 采集器错误数 |

---

## 9. 安全设计

### 9.1 IAM 最小权限

| Lambda | 权限 |
|--------|------|
| Collectors | DynamoDB:PutItem (signals), SSM:GetParameter (API Keys) |
| GPRI Engine | DynamoDB:Query (signals), DynamoDB:PutItem (gpri), SNS:Publish, CloudWatch:PutMetricData |
| Slack Notifier | SSM:GetParameter (webhook URL) |
| Probes | ec2:DescribeRegions, s3:HeadBucket (跨 Region) |

### 9.2 网络

- 所有 Lambda 无需 VPC（调用公网 API + DynamoDB + SNS）
- 无入站流量（纯 Scheduler 驱动）
- 出站到外部 API 走 NAT Gateway 免（Lambda 默认出站）

### 9.3 加密

- DynamoDB：AWS managed KMS 加密（默认）
- SSM SecureString：AWS managed KMS
- SNS：传输加密 (TLS)

---

## 10. 可观测性

### 10.1 日志

- 所有 Lambda 使用 `aws-lambda-powertools` 结构化日志
- CloudWatch Logs 保留期：30 天
- 关键字段：`region`, `signal_class`, `gpri`, `level`, `source`

### 10.2 指标

- Lambda 原生指标（Duration, Errors, Throttles）
- 自定义指标（见 8.2）
- CloudWatch Alarm：采集器连续 3 次失败 → 告警

### 10.3 追踪

- X-Ray 主动追踪（Lambda 配置 `tracing=Tracing.ACTIVE`）
- 追踪从 Scheduler → Collector → DynamoDB 全链路

---

## 11. 测试策略

### 11.1 单元测试

| 测试 | 内容 | Mock |
|------|------|------|
| test_gpri_calculator | 输入 7 维度分数，验证 GPRI 和级别 | 无 |
| test_weather_scoring | 温度/降雨/风速 → 分数映射 | Mock Open-Meteo 响应 |
| test_conflict_scoring | 事件数/基线比 → 异常倍数 → 分数 | Mock ACLED 响应 |
| test_adjudication | GPRI × 技术信号矩阵 → 行动建议 | 无 |
| test_region_config | 34 Region 配置完整性校验 | 无 |

### 11.2 集成测试

- 部署到 dev 环境后，手动注入高分信号到 DynamoDB
- 验证 GPRI 引擎正确计算，SNS 正确发送，Slack 收到告警

### 11.3 E2E 测试

- 等所有 Collector 运行一个完整周期（10 分钟）
- 验证 signals 表有数据，gpri 表有评分，Dashboard 有图

---

## 12. 实施计划

### Week 1: 基础设施 + 天气采集器（最小可验证）

```
Day 1:
  - CDK 项目初始化 (cdk init --language python)
  - DynamoDB 表 (signals + gpri)
  - Region 配置 (region_config.py, 34 Region)
  - shared/ 模块 (db.py, types.py, http_client.py)

Day 2:
  - weather.py 采集器 (Open-Meteo + GDACS + USGS)
  - Lambda + EventBridge Scheduler
  - 部署验证：weather 数据写入 signals 表

Day 3:
  - GPRI Engine (先只算 E 类)
  - 部署验证：gpri 表有评分
  - CloudWatch 自定义指标

Day 4-5:
  - SNS Topic + Slack Notifier
  - 基础 CloudWatch Dashboard
  - 端到端验证：天气异常 → GPRI 升高 → Slack 告警
```

### Week 2: 信号扩充

```
Day 1: conflict.py (ACLED API) — 注册 ACLED Key
Day 2: bgp.py (Cloudflare Radar + IODA) — 注册 CF Key
Day 3: infrastructure.py (海缆健康)
Day 4: cyber.py (abuse.ch) + compliance.py (OFAC/EU RSS)
Day 5: political.py (旅行预警) + GPRI 引擎扩展到 7 维度
```

### Week 3: 仪表板 + 联调

```
Day 1-2: CloudWatch Dashboard 完善 (全部 Region Gauge + 趋势)
Day 3: 告警规则完善 (级别变化 + 采集器健康)
Day 4: 单元测试补全
Day 5: 端到端测试 (人工注入高分信号验证全流程)
```

### Week 4: 技术探针 + 收尾

```
Day 1-2: 跨 Region Lambda 探针 (可选)
Day 3: 综合研判矩阵 (GPRI × 技术信号)
Day 4: 文档收尾 (README, 运维手册)
Day 5: Code Review + 上线
```

---

## 13. 成本估算

### PoC 阶段（单环境）

| 组件 | 规格 | 月调用量 | 月成本 |
|------|------|---------|--------|
| Lambda | 9 函数, arm64, 256MB | ~135K 次 | $0.50 |
| DynamoDB | On-Demand, 2 表 | ~600K WCU + RCU | $1.50 |
| EventBridge | 9 Scheduler Rules | ~135K 触发 | $0.13 |
| SNS | Standard Topic | ~1K 消息 | $0.01 |
| CloudWatch Dashboard | 1 Dashboard | 1 个 | $3.00 |
| CloudWatch Metrics | ~50 Custom Metrics | ~900K datapoints | $0.30 |
| SSM Parameter Store | 3 SecureString | 3 个 | Free |
| X-Ray | Traces | ~135K traces | Free Tier |
| **合计** | | | **~$5.50/月** |

### 生产阶段（加密 + 备份 + 告警）

预计 $15-25/月。

---

## 14. 依赖与前置条件

| 依赖 | 状态 | 说明 |
|------|------|------|
| AWS CDK v2 | ✅ 已安装 | `npm install -g aws-cdk` |
| Python 3.13 | ✅ 已安装 | Lambda Runtime |
| AWS Account YOUR_ACCOUNT_ID | ✅ 可用 | 部署: us-west-2, 被监控: ap-northeast-1 等 34 Region |
| ACLED API Key | ⏳ 需注册 | acleddata.com |
| Cloudflare Radar Token | ⏳ 需注册 | dash.cloudflare.com |
| Slack Incoming Webhook URL | ⏳ 需配置 | Slack App |

---

## 15. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 外部 API 不可用 | 信号缺失 | 每个 Collector 有 try/except，失败只记日志不阻塞 |
| ACLED Key 申请被拒 | A 类信号缺失 | 降级用 UCDP（完全免费），覆盖度略低 |
| API 限流 | 采集不完整 | http_client.py 内置指数退避重试 |
| DynamoDB 热分区 | 写入限流 | On-Demand 自动扩容 + PK 设计按 Region 分散 |
| Lambda 冷启动 | 采集延迟 | arm64 + 256MB，冷启动 <1s，可接受 |
