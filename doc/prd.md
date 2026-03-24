# AWS 跨 Region 容灾预警系统 — 地缘政治风险增强版

> **Product Requirements Document (PRD)**
> 版本：v0.1 | 日期：2026-03-24
> 作者：

---

## 1. 产品愿景

### 1.1 一句话定义

**在 AWS 基础设施出现技术故障之前，通过地缘政治、极端天气、网络安全等非技术信号，提前数小时到数天发出预警，驱动跨 Region 容灾决策。**

### 1.2 核心问题

传统技术监控体系有一个根本性盲区：

```
技术探针感知故障的时间点 = AWS 基础设施已经受损之后
地缘政治风险可以在"技术故障发生前 数小时到数天"就给出信号
```

典型事件时间线（中东中断事件还原）：

```
T-72h  地区局势升温，新闻媒体已有报道
T-48h  某国政府发布网络安全预警
T-24h  部分金融机构已开始内部讨论风险
T-0    AWS me-central-1 / me-south-1 出现故障
T+45m  AWS 官方 Health Dashboard 确认
T+90m  客户开始执行 DR 切换 ← 大多数人在这里
```

**目标：把感知时间从 T+45m 推前到 T-48h。**

### 1.3 目标用户

| 角色 | 需求 |
|------|------|
| AWS 客户 SRE/运维 | 更早的容灾切换信号，减少 MTTR |
| AWS SA/TAM | 为客户提供主动风险预警服务 |
| 金融/合规机构 | 满足监管数据主权、事件报告义务 |

---

## 2. 四层架构

```
┌─────────────────────────────────────────────────────────┐
│          Layer 0：地缘政治风险感知层                      │
│  武装冲突 │ 网络安全 │ 政治外交 │ 物理基础设施            │
│  极端天气 │ 合规法规 │ BGP/骨干网异常                    │
└─────────────────────────┬───────────────────────────────┘
                          │ GPRI 风险评分 (0-100)
┌─────────────────────────▼───────────────────────────────┐
│          Layer 1：技术信号采集层                          │
│  AWS Health │ 自研探针 │ 社区情报 │ 第三方监控            │
└─────────────────────────┬───────────────────────────────┘
                          │ 原始技术事件流
┌─────────────────────────▼───────────────────────────────┐
│          Layer 2：综合研判引擎                            │
│  GPRI × 技术信号 → 综合置信度 → 分级告警                 │
└─────────────────────────┬───────────────────────────────┘
                          │ 告警 + 行动建议
┌─────────────────────────▼───────────────────────────────┐
│          Layer 3：行动触发层                              │
│  预授权切换 │ 主动撤离 │ 资产保护 │ 监管合规              │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Layer 0：地缘政治风险感知层

### 3.1 七类信号源

#### A 类：武装冲突与战争信号

| 信号源 | 更新频率 | 接入方式 | 免费/付费 |
|--------|---------|---------|----------|
| ACLED（武装冲突地点与事件数据库） | 每日 | REST API | 免费 API Key |
| UCDP（乌普萨拉冲突数据库） | 每日 | REST API | 免费 |
| GDELT Project | 实时 | BigQuery / REST | 免费 |
| UN OCHA ReliefWeb | 每日 | REST API | 免费 |

**WorldMonitor 已有实现：** `conflict` 服务（ACLED + UCDP），`intelligence` 服务（GDELT），`displacement` 服务（难民数据）。可直接复用其 RPC 定义和 API 端点。

**采集逻辑：**
- 每 10 分钟拉取目标 Region 关联国家的冲突事件
- 对比基线：近 7 日事件数 vs 过去 90 日均值，计算异常倍数
- 关注致死事件数、事件类型升级（抗议 → 武装冲突）

#### B 类：网络安全威胁信号

| 信号源 | 说明 | 免费/付费 |
|--------|------|----------|
| CISA 预警公告 | 美国网络安全局，国家级威胁预警 | 免费 RSS |
| Cloudflare Radar | DDoS 趋势、BGP 异常、断网统计 | 免费 API |
| abuse.ch（Feodo/URLhaus） | 恶意软件 C2、钓鱼 URL | 免费 API |

**WorldMonitor 已有实现：** `cyber` 服务（Feodo/URLhaus/C2Intel/OTX/AbuseIPDB），可复用。

**采集逻辑：**
- 监控目标 Region 国家 IP 段的威胁密度变化
- 大规模扫描活动频率（Shodan/Censys 基线对比）
- CERT/CC 预警级别提升

#### C 类：政治与外交信号

| 信号源 | 说明 | 免费/付费 |
|--------|------|----------|
| 美国国务院旅行警告 | Level 1-4，Level 3/4 = 高风险 | 免费 |
| 英国 FCDO 旅行建议 | 类似，覆盖全球 | 免费 |
| 联合国安理会决议 | 制裁、军事行动授权 | 免费 |

**WorldMonitor 已有实现：** `intelligence` 服务中的 `list_security_advisories` RPC，覆盖多国旅行预警。

**采集逻辑：**
- 每小时检查目标国家旅行预警级别变化
- Level 升级（2→3 或 3→4）立即触发 GPRI 重算

#### D 类：物理基础设施威胁

| 信号源 | 说明 | 免费/付费 |
|--------|------|----------|
| TeleGeography 海缆地图 | 海底光缆中断事件 | 免费 |
| UKMTO | 红海/亚丁湾海事安全公告 | 免费 |
| 航运 AIS 数据 | 船舶轨迹异常（战区绕行） | 免费/付费 |

**WorldMonitor 已有实现：** `infrastructure` 服务的 `get_cable_health` RPC（海缆健康评分 + 证据链），`maritime` 服务（AIS 船舶数据、航行警告）。直接复用。

**采集逻辑：**
- 监控途经 AWS Region 的关键海缆状态（SEA-ME-WE 5、AAE-1、EIG 等）
- 海缆状态从 `ok` 降级到 `degraded` 或 `fault` 时触发告警
- 航运预警覆盖红海/亚丁湾（胡塞武装威胁区域）

#### E 类：极端天气与自然灾害

| 信号源 | 预警时间 | 免费/付费 |
|--------|---------|----------|
| Open-Meteo API | 72h | 免费，无需注册 |
| GDACS（全球灾害预警协调系统） | 实时 | 免费 RSS/API |
| USGS 地震 API | 实时（震后） | 免费 |
| GloFAS（全球洪水感知系统） | 30 天 | 免费 |

**WorldMonitor 已有实现：** `climate` 服务（气候异常），`seismology` 服务（地震），`forecast` 服务（天气预报），`natural` 服务（EONET 自然事件），`wildfire` 服务。

**采集逻辑：**
- 每小时检查目标 Region 坐标 72h 天气预报
- 阈值：温度 ≥ 45°C（冷却系统风险）、降雨 ≥ 20mm/h（洪水）、风速 ≥ 100km/h
- GDACS 橙色/红色级别灾害 → 直接推高 GPRI

#### F 类：合规法规风险

| 信号源 | 说明 | 免费/付费 |
|--------|------|----------|
| OFAC 制裁名单（SDN List） | 美国财政部制裁变更 | 免费 RSS |
| EU 制裁公报 | 欧盟制裁 | 免费 RSS |
| AWS 合规声明 | 区域政策变更 | 免费 RSS |

**WorldMonitor 已有实现：** `sanctions` 服务（OFAC SDN List 聚合 + 国家/项目维度压力分析），可直接复用。

**采集逻辑：**
- 每 30 分钟检查 OFAC RSS 和 EU 制裁公报
- 目标 Region 关联国家出现新制裁 → 触发 COMPLIANCE_BLOCK 标志
- COMPLIANCE_BLOCK = True 时，DR 切换需法务审批，不得自动执行

#### G 类：互联网骨干 / BGP 路由异常

| 信号源 | 说明 | 免费/付费 |
|--------|------|----------|
| Cloudflare Radar（BGP 模块） | BGP 前缀撤销、路由变化 | 免费 API |
| IODA（Georgia Tech） | 国家级断网检测 | 免费 API |
| RIPE Atlas | 全球探针网络，IP 可达性 | 免费 API |

**WorldMonitor 已有实现：** `infrastructure` 服务的 `list_internet_outages`（断网检测）、`list_internet_traffic_anomalies`（流量异常）、`list_internet_ddos_attacks`（DDoS 攻击检测）。

**采集逻辑：**
- 监控 AWS ASN（AS16509/AS14618）的 BGP 路由变更量
- 目标国家 IODA 评分骤降 → 基础设施层故障早期信号
- G 类信号的时效优势：BGP 变化 1-2 分钟可见，AWS Health 通常 20-60 分钟

> **G 类是时效最强的信号——往往比 AWS 官方通告早 15-30 分钟发现问题。**

### 3.2 WorldMonitor 复用矩阵

| 信号类 | WorldMonitor 服务 | 复用度 | 需新增 |
|--------|------------------|--------|--------|
| A 武装冲突 | conflict (ACLED/UCDP), displacement | ⭐⭐⭐ 直接复用 | 异常倍数计算 |
| B 网络安全 | cyber (Feodo/URLhaus/OTX) | ⭐⭐⭐ 直接复用 | Region 关联过滤 |
| C 政治外交 | intelligence (security_advisories) | ⭐⭐ 部分复用 | 旅行预警级别解析 |
| D 物理基础设施 | infrastructure (cable_health), maritime | ⭐⭐⭐ 直接复用 | 海缆→Region 映射 |
| E 极端天气 | climate, seismology, forecast, natural | ⭐⭐⭐ 直接复用 | Region 坐标阈值 |
| F 合规法规 | sanctions | ⭐⭐ 部分复用 | OFAC RSS + COMPLIANCE_BLOCK |
| G BGP/骨干网 | infrastructure (outages, traffic_anomalies, ddos) | ⭐⭐⭐ 直接复用 | ASN 过滤 |

**结论：WorldMonitor 已覆盖约 70% 的数据采集需求。** 核心新增工作集中在：
1. GPRI 评分引擎（聚合七类信号 → 0-100 分）
2. Region 映射层（信号 → AWS Region 关联）
3. 行动触发层（GPRI × 技术信号 → 告警/切换决策）

---

## 4. Layer 1：技术信号采集层

### 4.1 信号源

| 信号源 | 方式 | 说明 |
|--------|------|------|
| AWS Health API | `aws health describe-events` | 官方事件通告 |
| CloudWatch 跨 Region 探针 | Lambda + CW Alarm | 主动可用性探测 |
| AWS Status Page | RSS / 网页监控 | 公开状态页 |
| 第三方（Downdetector 等） | 网页监控 / API | 社区故障报告 |

### 4.2 自研探针设计

每个监控 Region 部署 Lambda 探针，每分钟执行：
- EC2 API 可达性（DescribeInstances）
- S3 API 可达性（HeadBucket）
- RDS API 可达性（DescribeDBInstances）
- 跨 Region 网络延迟（从备用 Region 到目标 Region）

探针结果写入备用 Region 的 DynamoDB，确保控制平面独立于被监控 Region。

---

## 5. Layer 2：综合研判引擎

### 5.1 GPRI 评分模型

```
地缘政治风险指数 (GPRI) = 0~100

维度权重：
  A. 武装冲突强度      20 分  ← ACLED 事件频率 + 烈度异常倍数
  B. 网络威胁态势      15 分  ← 威胁密度 + CERT 预警级别
  C. 政治稳定性        15 分  ← 旅行预警级别 + 外交事件
  D. 物理基础设施      10 分  ← 海缆状态 + 电力稳定性
  E. 极端天气          15 分  ← 气象预警 + 灾害事件
  F. 合规法规风险      10 分  ← 制裁变更 + 数据主权
  G. BGP/骨干网异常    15 分  ← 路由撤销 + 断网检测
```

### 5.2 GPRI 阈值与行动

| GPRI 范围 | 级别 | 含义 | 系统动作 |
|-----------|------|------|---------|
| 0-30 | 🟢 GREEN | 正常运营 | 常规监控 |
| 31-50 | 🟡 YELLOW | 提升关注 | Review DR 就绪状态，加密频次 |
| 51-70 | 🟠 ORANGE | 主动备战 | 降低 TTL，Scale Up 备用 Region |
| 71-85 | 🔴 RED | 建议撤离 | 启动切换决策流程，通知利益相关人 |
| 86-100 | ⚫ BLACK | 立即执行 | 不等技术故障，直接执行 DR |

### 5.3 综合研判矩阵

```
                    技术信号
                    正常        异常        确认故障
                ┌──────────┬──────────┬──────────────┐
   GREEN(0-30)  │  正常运营  │  技术排查  │  标准 DR 切换 │
   YELLOW(31-50)│  加强监控  │  预备切换  │  优先 DR 切换 │
   ORANGE(51-70)│  预置备战  │  建议切换  │  立即切换     │
   RED(71-85)   │  主动撤离  │  立即切换  │  立即切换     │
   BLACK(86+)   │  立即切换  │  立即切换  │  立即切换     │
                └──────────┴──────────┴──────────────┘

关键原则：GPRI ≥ 71（RED）时，不等技术故障发生，主动启动切换。
```

### 5.4 COMPLIANCE_BLOCK 机制

当 F 类信号触发 COMPLIANCE_BLOCK 时：
- 所有自动化切换动作暂停
- 必须法务/合规团队审批后才能执行
- 审批结果计入审计日志
- 适用场景：新制裁导致 DR 目标 Region 存在合规风险

---

## 6. Layer 3：行动触发层

### 6.1 两种切换模式

| 模式 | 触发条件 | 时间窗口 | 数据保证 |
|------|---------|---------|---------|
| **有序迁移** | GPRI 持续 ≥ ORANGE，或 RED 且有时间 | 4-72 小时 | RPO ≈ 0（双写） |
| **紧急切换** | 技术故障确认，或 BLACK | 分钟级 | RPO ≤ 15min |

### 6.2 有序迁移流程

```
1. GPRI 升至 ORANGE → 备用 Region Scale Up
2. 启动双写模式 → 确保数据一致性
3. 逐步切流（10% → 50% → 100%）→ 验证备用 Region 健康
4. Route53 TTL 逐步降低（300s → 120s → 60s）
5. 完成全量切换 → 旧环境只读保留 30 天
```

### 6.3 紧急切换流程

```
1. 技术故障确认 或 GPRI = BLACK → 立即触发
2. Route53 DNS 更新（Failover Policy）
3. 数据库 Failover（Aurora Global/DynamoDB Global）
4. 通知利益相关人
5. 旧环境只读保留 7 天
```

### 6.4 预授权触发条件（客户配置）

```yaml
proactive_evacuation_triggers:
  # 旅行警告升至 Level 4
  - trigger: state_dept_advisory >= 4
    action: initiate_controlled_migration
    notice_hours: 48

  # 军事打击距数据中心 50km 内
  - trigger: military_strike_within_50km
    action: emergency_failover
    notice_hours: 0

  # 关键海缆中断 + 延迟暴增
  - trigger: submarine_cable_fault AND latency_increase > 200ms
    action: initiate_controlled_migration
    notice_hours: 12

  # GPRI 持续 RED 超 24 小时
  - trigger: gpri >= 71 AND duration >= 24h
    action: recommend_migration
    notice_hours: 72
```

---

## 7. 全量 Region 风险画像

系统覆盖全部 AWS 商业 Region（34 个，不含 GovCloud 和中国区）。每个 Region 配置：
- **地理坐标**（数据中心大致位置，用于天气/地震半径检测）
- **关联国家**（ISO 3166-1 alpha-2，用于 A-C/F 类信号过滤）
- **基线风险等级**（基于地缘政治环境的静态底分）
- **关键海缆**（D 类信号关联）
- **推荐备用 Region**（DR 目标）

### 7.1 全量 Region 配置表

#### 高风险区域（基线 GPRI ≥ 15）

| Region | 城市 | 坐标 | 关联国家 | 基线风险 | 主要风险因素 | 推荐 DR |
|--------|------|------|---------|---------|-------------|---------|
| me-central-1 | UAE 迪拜 | 25.2, 55.3 | AE | 20 | 伊朗冲突溢出、胡塞导弹/无人机、海缆中断、极端高温/洪水 | ap-southeast-1 |
| me-south-1 | Bahrain | 26.2, 50.6 | BH | 18 | 伊朗-沙特代理冲突、什叶派动荡、美第五舰队驻地 | ap-southeast-1 |
| il-central-1 | Israel 特拉维夫 | 32.1, 34.8 | IL | 25 | 以巴冲突、伊朗直接打击、安全局势波动 | eu-west-1 |
| af-south-1 | 南非 开普敦 | -33.9, 18.4 | ZA | 15 | 电力不稳（Eskom）、社会治安、极端天气 | eu-west-1 |

#### 中风险区域（基线 GPRI 8-14）

| Region | 城市 | 坐标 | 关联国家 | 基线风险 | 主要风险因素 | 推荐 DR |
|--------|------|------|---------|---------|-------------|---------|
| ap-east-1 | 香港 | 22.3, 114.2 | HK | 12 | 台海地缘风险、台风季、中美关系 | ap-southeast-1 |
| ap-east-2 | 韩国 首尔（新） | 37.6, 127.0 | KR | 10 | 朝鲜半岛紧张局势 | ap-northeast-1 |
| ap-south-1 | 印度 孟买 | 19.1, 72.9 | IN | 10 | 季风洪水、印巴紧张、高温 | ap-southeast-1 |
| ap-south-2 | 印度 海得拉巴 | 17.4, 78.5 | IN | 10 | 同上 | ap-southeast-1 |
| ap-southeast-3 | 印尼 雅加达 | -6.2, 106.8 | ID | 10 | 地震带、火山、洪水 | ap-southeast-1 |
| ap-southeast-4 | 澳大利亚 墨尔本 | -37.8, 145.0 | AU | 8 | 山火季（11-3月）、极端高温 | ap-southeast-2 |
| ap-southeast-5 | 新西兰 奥克兰（新） | -36.9, 174.8 | NZ | 8 | 地震带、台风 | ap-southeast-2 |
| ap-southeast-6 | 泰国 曼谷（新） | 13.8, 100.5 | TH | 9 | 洪水季、政治动荡 | ap-southeast-1 |
| ap-southeast-7 | 马来西亚（新） | 3.1, 101.7 | MY | 8 | 洪水季 | ap-southeast-1 |
| sa-east-1 | 巴西 圣保罗 | -23.5, -46.6 | BR | 10 | 暴雨洪水、社会治安 | us-east-1 |
| eu-south-1 | 意大利 米兰 | 45.5, 9.2 | IT | 8 | 地震带、极端高温 | eu-central-1 |
| eu-south-2 | 西班牙 萨拉戈萨 | 41.7, -0.9 | ES | 8 | 极端高温、野火 | eu-west-1 |
| mx-central-1 | 墨西哥（新） | 19.4, -99.1 | MX | 10 | 地震带、社会治安 | us-east-1 |

#### 低风险区域（基线 GPRI < 8）

| Region | 城市 | 坐标 | 关联国家 | 基线风险 | 主要风险因素 | 推荐 DR |
|--------|------|------|---------|---------|-------------|---------|
| us-east-1 | 弗吉尼亚 | 38.9, -77.5 | US | 3 | 飓风季（6-11月）、冬季暴风雪 | us-west-2 |
| us-east-2 | 俄亥俄 | 39.9, -82.6 | US | 2 | 极端天气（龙卷风） | us-west-2 |
| us-west-1 | 加州 | 37.3, -121.9 | US | 4 | 地震带、野火、干旱 | us-west-2 |
| us-west-2 | 俄勒冈 | 45.6, -122.3 | US | 2 | 冬季暴风雪 | us-east-1 |
| ca-central-1 | 加拿大 蒙特利尔 | 45.5, -73.6 | CA | 2 | 冬季极寒 | us-east-1 |
| ca-west-1 | 加拿大 卡尔加里（新） | 51.0, -114.1 | CA | 2 | 冬季极寒、野火 | us-west-2 |
| eu-central-1 | 德国 法兰克福 | 50.1, 8.7 | DE | 3 | 极端高温（夏季）、能源价格波动 | eu-west-1 |
| eu-central-2 | 瑞士 苏黎世（新） | 47.4, 8.5 | CH | 2 | 极端天气少，政治极稳定 | eu-central-1 |
| eu-west-1 | 爱尔兰 都柏林 | 53.3, -6.3 | IE | 2 | 冬季风暴 | eu-central-1 |
| eu-west-2 | 英国 伦敦 | 51.5, -0.1 | GB | 3 | 冬季风暴、洪水 | eu-west-1 |
| eu-west-3 | 法国 巴黎 | 48.9, 2.3 | FR | 3 | 极端高温（夏季）、社会运动 | eu-central-1 |
| eu-north-1 | 瑞典 斯德哥尔摩 | 59.3, 18.1 | SE | 3 | 俄乌冲突邻近、冬季极寒 | eu-central-1 |
| ap-northeast-1 | 日本 东京 | 35.7, 139.7 | JP | 6 | 台风（8-10月）、地震（全年）| ap-northeast-2 |
| ap-northeast-2 | 韩国 首尔 | 37.6, 127.0 | KR | 5 | 朝鲜半岛紧张 | ap-northeast-1 |
| ap-northeast-3 | 日本 大阪 | 34.7, 135.5 | JP | 6 | 同 ap-northeast-1 | ap-southeast-1 |
| ap-southeast-1 | 新加坡 | 1.3, 103.8 | SG | 2 | 政治极稳定，海缆丰富 | ap-southeast-2 |
| ap-southeast-2 | 澳大利亚 悉尼 | -33.9, 151.2 | AU | 3 | 山火季、极端高温 | ap-southeast-1 |

> **注**：标"（新）"的 Region 为 opt-in 或新开 Region，部分尚未正式 GA。坐标为数据中心大致位置。

### 7.2 基线 GPRI 说明

基线风险是静态底分，反映该 Region 所在地理位置的长期地缘政治环境。动态 GPRI 在基线上叠加七类实时信号。例如：

```
me-central-1 动态 GPRI = 基线(20) + A类(+8) + B类(+3) + E类(+5) = 36 → YELLOW
ap-southeast-1 动态 GPRI = 基线(2) + 各类(0) = 2 → GREEN
```

### 7.3 各 Region 极端天气风险矩阵

| 天气类型 | 高风险 Region | 预警窗口 |
|---------|--------------|---------|
| 飓风/台风 | us-east-1, ap-northeast-1/3, ap-east-1, ap-southeast-3/6 | 48-120h |
| 地震 | ap-northeast-1/3, us-west-1, ap-southeast-3, sa-east-1, eu-south-1, mx-central-1 | 0（无法预测） |
| 极端高温 | me-central-1, me-south-1, ap-south-1/2, eu-central-1, eu-west-3, eu-south-1/2 | 48-96h |
| 洪水/暴雨 | me-central-1, ap-south-1, ap-southeast-3/6, sa-east-1, eu-west-2 | 24-72h |
| 野火/山火 | us-west-1, ap-southeast-2/4, ca-west-1, eu-south-2 | 1-7 天 |
| 冬季暴风雪 | us-east-1/2, ca-central-1, ca-west-1, eu-north-1, eu-west-1/2 | 48-72h |
| 沙尘暴 | me-central-1, me-south-1 | 6-24h |

### 7.4 关键海缆与 Region 映射

| 海缆 | 路由 | 关联 Region | 风险区域 |
|------|------|------------|---------|
| SEA-ME-WE 5 | 东南亚 ↔ 中东 ↔ 欧洲 | me-central-1, ap-southeast-1, eu-west-1 | 红海/亚丁湾 |
| SEA-ME-WE 6 | 东南亚 ↔ 中东 ↔ 欧洲（新建） | me-central-1, ap-southeast-1 | 同上 |
| AAE-1 | 亚洲 ↔ 非洲 ↔ 欧洲 | me-central-1, me-south-1, ap-southeast-1 | 红海/亚丁湾 |
| EIG | 欧洲 ↔ 印度 ↔ 亚洲 | me-south-1, eu-west-1, ap-south-1 | 红海 |
| FLAG/FALCON | 中东 ↔ 印度 | me-central-1, me-south-1, ap-south-1 | 波斯湾 |
| MAREA | 美国 ↔ 欧洲 | us-east-1, eu-west-2 | 北大西洋（低风险） |
| Equiano | 欧洲 ↔ 非洲 | eu-west-1, af-south-1 | 西非海域 |
| Jupiter | 美国 ↔ 亚洲 | us-west-2, ap-northeast-1, ap-southeast-1 | 太平洋（低风险） |
| PLCN | 美国 ↔ 亚洲 | us-west-1, ap-east-1 | 太平洋 |
| SJC2 | 新加坡 ↔ 日本 ↔ 中国 | ap-southeast-1, ap-northeast-1, ap-east-1 | 南海 |
| Monet | 美国 ↔ 巴西 | us-east-1, sa-east-1 | 南大西洋（低风险） |

---

## 8. 通知与告警

### 8.1 告警渠道

| 级别 | 渠道 | 频率 |
|------|------|------|
| GREEN | Dashboard 展示 | 持续 |
| YELLOW | Slack/Teams 通知 | 每次变更 |
| ORANGE | Slack + 邮件 + PagerDuty | 每次变更 + 每小时摘要 |
| RED | Slack + 邮件 + PagerDuty + 电话 | 即时 |
| BLACK | 全渠道 + 自动执行 | 即时 |

### 8.2 告警内容

每条告警包含：
- GPRI 当前值及趋势（↑↓→）
- 触发维度及关键事件摘要
- 受影响 AWS Region
- 建议行动
- 一键操作链接（确认/升级/执行切换）

---

## 9. 金融客户特殊考量

### 9.1 监管合规

- **数据主权**：部分金融监管要求数据留存在特定 Region，主动迁移前需获监管豁免
- **事件报告义务**：DR 切换本身可能触发报告义务（N 小时内向监管报告）
- **审计留存**：所有切换决策完整记录（触发条件、决策人、执行时间、GPRI 快照）

### 9.2 双边关系风险

```
场景：以色列 vs 海湾国家客户
  同一 AWS 账户可能服务两类客户
  战争期间可能面临政治压力

建议：
  敏感客户使用独立 AWS 账户 + 独立 Region
  合同中明确 Force Majeure 条款
  法务团队评估 OFAC 制裁合规风险
```

---

## 10. 技术架构

### 10.1 部署架构

```
                    ┌─────────────────────────┐
                    │    Dashboard / Console    │
                    │  (React SPA / Grafana)    │
                    └────────────┬──────────────┘
                                 │
                    ┌────────────▼──────────────┐
                    │     API Gateway (HTTPS)    │
                    │   (备用 Region 部署)        │
                    └────────────┬──────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
    ┌─────────▼────────┐ ┌──────▼──────┐ ┌─────────▼────────┐
    │   GPRI Engine     │ │  Technical  │ │  Action Engine   │
    │  (Lambda/ECS)     │ │  Probes     │ │  (Step Functions)│
    │                   │ │  (Lambda)   │ │                  │
    │  7 类信号聚合     │ │  跨 Region  │ │  切换编排        │
    │  评分计算         │ │  可用性探测  │ │  通知分发        │
    └─────────┬────────┘ └──────┬──────┘ └─────────┬────────┘
              │                  │                  │
    ┌─────────▼──────────────────▼──────────────────▼────────┐
    │                    Data Store                           │
    │  DynamoDB (事件流/GPRI历史) + S3 (报告/快照)            │
    │  部署在备用 Region，独立于被监控 Region                  │
    └────────────────────────────────────────────────────────┘
```

### 10.2 关键设计原则

1. **控制平面独立**：所有监控组件部署在备用 Region（如 ap-southeast-1），不依赖被监控 Region
2. **多信号交叉验证**：单一信号不触发高级别告警，需至少两类信号关联确认
3. **人在环路（HITL）**：RED/BLACK 级别告警发出后，仍需人工确认执行（除非客户预授权全自动）
4. **审计完整性**：所有决策点记录完整上下文（GPRI 快照、信号明细、操作人、时间戳）
5. **渐进式部署**：支持从纯告警模式逐步升级到半自动、全自动

### 10.3 数据流

```
信号源 (A-G 类)
  │
  ▼
采集器 (Lambda/EventBridge Scheduler)
  │  每 10 分钟拉取
  ▼
原始事件 → DynamoDB (events table)
  │
  ▼
GPRI Engine (Lambda)
  │  每 5 分钟重算
  ├── 读取各类信号最新值
  ├── 按权重计算 GPRI
  ├── 对比上次值，检测变化
  └── 写入 GPRI 历史表
  │
  ▼
研判引擎 (Lambda)
  │  GPRI 变化时触发
  ├── 叠加技术信号
  ├── 查找匹配的预授权规则
  ├── 检查 COMPLIANCE_BLOCK
  └── 生成告警/行动建议
  │
  ▼
Action Engine (Step Functions)
  ├── 通知分发 (SNS → Slack/Email/PagerDuty/Phone)
  ├── 自动化动作 (Scale Up / TTL 调整 / 切换编排)
  └── 审计日志 (S3 + CloudWatch Logs)
```

### 10.4 WorldMonitor 集成方式

两种集成策略：

**方案 A：API 消费者模式（推荐 Phase 1）**
- 本系统作为 WorldMonitor 的 API 消费者
- 通过 WorldMonitor 的 Vercel Edge API 端点拉取数据
- 优点：零维护开销，数据源由 WorldMonitor 持续更新
- 缺点：依赖 WorldMonitor 可用性，延迟多一跳

**方案 B：数据管道模式（Phase 2+）**
- 抽取 WorldMonitor 的采集逻辑，独立部署在 AWS 上
- 用 EventBridge Scheduler 替代 Vercel Cron
- 用 DynamoDB 替代 Upstash Redis
- 优点：完全自主可控，低延迟
- 缺点：需维护 30+ 数据源集成

**推荐路径：Phase 1 用方案 A 快速验证，Phase 2 逐步迁移到方案 B。**

---

## 11. 分阶段实施计划

### Phase 0：立即可做（1 周）— 人工 + RSS

| 任务 | 工作量 | 产出 |
|------|--------|------|
| 订阅关键 RSS（国务院旅行警告、CISA、OFAC、GDACS） | 0.5 天 | Slack 频道推送 |
| 设置 Google Alerts（"AWS me-central-1 outage" 等） | 0.5 天 | 邮件通知 |
| 建立 Region 风险因素知识库 | 1 天 | Wiki/Confluence 页面 |
| 制定 GPRI 人工评分 SOP | 1 天 | Runbook |
| 目标 Region 海缆地图标注 | 0.5 天 | 可视化文档 |

### Phase 1：自动化采集 + GPRI 仪表板（1 个月）

| 任务 | 工作量 | 产出 |
|------|--------|------|
| 接入 WorldMonitor API（conflict/cyber/infrastructure/climate/sanctions） | 5 天 | Lambda 采集器 |
| 实现 GPRI 评分引擎 | 3 天 | Lambda + DynamoDB |
| Region 映射配置（信号 → AWS Region） | 2 天 | 配置文件 |
| GPRI 仪表板（Grafana 或 React SPA） | 5 天 | Dashboard |
| 告警通知集成（Slack + 邮件） | 2 天 | SNS + Lambda |
| 技术探针部署（Lambda 跨 Region） | 3 天 | CW Alarms |

### Phase 2：研判引擎 + DR 集成（3 个月）

| 任务 | 工作量 | 产出 |
|------|--------|------|
| 综合研判引擎（GPRI × 技术信号矩阵） | 5 天 | Step Functions |
| 预授权规则引擎 | 3 天 | 配置化触发条件 |
| COMPLIANCE_BLOCK 机制 | 2 天 | 法务审批流程 |
| DR 切换编排集成（Route53/Aurora/DynamoDB） | 10 天 | Step Functions |
| 有序迁移自动化（双写 + 逐步切流） | 10 天 | 编排脚本 |
| 审计日志系统 | 3 天 | S3 + CloudWatch |
| GameDay 演练（模拟 GPRI RED 场景） | 3 天 | 演练报告 |

### Phase 3：高级功能（6 个月）

| 任务 | 说明 |
|------|------|
| 数据管道独立化 | 从 WorldMonitor API 迁移到自建采集 |
| ML 异常���测 | 基于历史 GPRI 模式预测风险升级 |
| 多客户 SaaS 化 | 支持多租户 Region 配置 |
| 移动端告警 | iOS/Android 推送 |

---

## 12. 成功指标

| 指标 | 基线（当前） | Phase 1 目标 | Phase 2 目标 |
|------|-------------|-------------|-------------|
| 风险感知提前量 | T+45m | T-24h | T-48h |
| GPRI 更新频率 | 无 | 5 分钟 | 1 分钟 |
| 信号源覆盖 | 0 | 7 类全覆盖 | 7 类 + ML 增强 |
| DR 切换决策时间 | > 90 分钟 | < 30 分钟 | < 5 分钟（预授权） |
| 误报率 | N/A | < 5% ORANGE+ | < 2% ORANGE+ |

---

## 13. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| WorldMonitor API 不可用 | Phase 1 数据中断 | 关键信号源保留本地缓存 + 降级到直接 API |
| GPRI 误报导致不必要切换 | 业务中断 + 信任损失 | RED/BLACK 需人工确认，ORANGE 仅预备 |
| 制裁合规未及时阻断 | 法律风险 | COMPLIANCE_BLOCK 默认阻断，需显式审批 |
| 信号源 API 变更 | 采集中断 | 每个信号源有 fallback + 健康检查 |
| 金融监管数据主权冲突 | 无法合法迁移 | 提前评估目标 Region 合规性，预审批 |

---

## 14. 附录

### A. GPRI 评分细则

```
A. 武装冲突强度 (0-20)
   0-5:   近 7 天事件数 ≤ 90 天均值
   6-10:  事件数 = 1.5-3 倍均值
   11-15: 事件数 > 3 倍均值，或出现高致死事件
   16-20: 活跃战争状态，致死事件持续

B. 网络威胁态势 (0-15)
   0-5:   正常威胁水平
   6-10:  CERT 发布预警，或目标国家威胁密度上升 >50%
   11-15: 大规模 DDoS 或国家级网络攻击活动

C. 政治稳定性 (0-15)
   0-5:   旅行预警 Level 1-2
   6-10:  旅行预警 Level 3
   11-15: 旅行预警 Level 4，或撤侨令

D. 物理基础设施 (0-10)
   0-3:   所有海缆 OK
   4-7:   海缆 degraded 或航运预警
   8-10:  海缆 fault 或关键基础设施受损

E. 极端天气 (0-15)
   0-5:   无预警或低级别
   6-10:  橙色预警（强降雨/高温/沙尘暴）
   11-15: 红色预警，或 GDACS 红色灾害，或强震（M≥5.0 100km内）

F. 合规法规风险 (0-10)
   0-3:   无变更
   4-7:   新制裁涉及关联国家
   8-10:  制裁直接限制 DR 目标 Region 运营

G. BGP/骨干网异常 (0-15)
   0-5:   路由变更在正常范围
   6-10:  BGP 路由撤销量 > P99 × 3
   11-15: 国家级断网或 AWS ASN 大规模路由异常
```

### B. 全量 Region 坐标配置（JSON 格式）

```jsonc
// 系统启动时加载，每个 Region 的静态配置
// 新增 Region 时只需添加一行即可
{
  "regions": {
    "us-east-1":      { "city": "Virginia",      "lat": 38.9,  "lon": -77.5,  "country": "US", "baseline": 3  },
    "us-east-2":      { "city": "Ohio",           "lat": 39.9,  "lon": -82.6,  "country": "US", "baseline": 2  },
    "us-west-1":      { "city": "N. California",  "lat": 37.3,  "lon": -121.9, "country": "US", "baseline": 4  },
    "us-west-2":      { "city": "Oregon",         "lat": 45.6,  "lon": -122.3, "country": "US", "baseline": 2  },
    "ca-central-1":   { "city": "Montreal",       "lat": 45.5,  "lon": -73.6,  "country": "CA", "baseline": 2  },
    "ca-west-1":      { "city": "Calgary",        "lat": 51.0,  "lon": -114.1, "country": "CA", "baseline": 2  },
    "eu-central-1":   { "city": "Frankfurt",      "lat": 50.1,  "lon": 8.7,    "country": "DE", "baseline": 3  },
    "eu-central-2":   { "city": "Zurich",         "lat": 47.4,  "lon": 8.5,    "country": "CH", "baseline": 2  },
    "eu-west-1":      { "city": "Dublin",         "lat": 53.3,  "lon": -6.3,   "country": "IE", "baseline": 2  },
    "eu-west-2":      { "city": "London",         "lat": 51.5,  "lon": -0.1,   "country": "GB", "baseline": 3  },
    "eu-west-3":      { "city": "Paris",          "lat": 48.9,  "lon": 2.3,    "country": "FR", "baseline": 3  },
    "eu-north-1":     { "city": "Stockholm",      "lat": 59.3,  "lon": 18.1,   "country": "SE", "baseline": 3  },
    "eu-south-1":     { "city": "Milan",          "lat": 45.5,  "lon": 9.2,    "country": "IT", "baseline": 8  },
    "eu-south-2":     { "city": "Zaragoza",       "lat": 41.7,  "lon": -0.9,   "country": "ES", "baseline": 8  },
    "me-central-1":   { "city": "Dubai",          "lat": 25.2,  "lon": 55.3,   "country": "AE", "baseline": 20 },
    "me-south-1":     { "city": "Bahrain",        "lat": 26.2,  "lon": 50.6,   "country": "BH", "baseline": 18 },
    "il-central-1":   { "city": "Tel Aviv",       "lat": 32.1,  "lon": 34.8,   "country": "IL", "baseline": 25 },
    "af-south-1":     { "city": "Cape Town",      "lat": -33.9, "lon": 18.4,   "country": "ZA", "baseline": 15 },
    "ap-east-1":      { "city": "Hong Kong",      "lat": 22.3,  "lon": 114.2,  "country": "HK", "baseline": 12 },
    "ap-east-2":      { "city": "Seoul (new)",    "lat": 37.6,  "lon": 127.0,  "country": "KR", "baseline": 10 },
    "ap-northeast-1": { "city": "Tokyo",          "lat": 35.7,  "lon": 139.7,  "country": "JP", "baseline": 6  },
    "ap-northeast-2": { "city": "Seoul",          "lat": 37.6,  "lon": 127.0,  "country": "KR", "baseline": 5  },
    "ap-northeast-3": { "city": "Osaka",          "lat": 34.7,  "lon": 135.5,  "country": "JP", "baseline": 6  },
    "ap-south-1":     { "city": "Mumbai",         "lat": 19.1,  "lon": 72.9,   "country": "IN", "baseline": 10 },
    "ap-south-2":     { "city": "Hyderabad",      "lat": 17.4,  "lon": 78.5,   "country": "IN", "baseline": 10 },
    "ap-southeast-1": { "city": "Singapore",      "lat": 1.3,   "lon": 103.8,  "country": "SG", "baseline": 2  },
    "ap-southeast-2": { "city": "Sydney",         "lat": -33.9, "lon": 151.2,  "country": "AU", "baseline": 3  },
    "ap-southeast-3": { "city": "Jakarta",        "lat": -6.2,  "lon": 106.8,  "country": "ID", "baseline": 10 },
    "ap-southeast-4": { "city": "Melbourne",      "lat": -37.8, "lon": 145.0,  "country": "AU", "baseline": 8  },
    "ap-southeast-5": { "city": "Auckland (new)", "lat": -36.9, "lon": 174.8,  "country": "NZ", "baseline": 8  },
    "ap-southeast-6": { "city": "Bangkok (new)",  "lat": 13.8,  "lon": 100.5,  "country": "TH", "baseline": 9  },
    "ap-southeast-7": { "city": "Malaysia (new)",  "lat": 3.1,  "lon": 101.7,  "country": "MY", "baseline": 8  },
    "sa-east-1":      { "city": "São Paulo",      "lat": -23.5, "lon": -46.6,  "country": "BR", "baseline": 10 },
    "mx-central-1":   { "city": "Mexico (new)",   "lat": 19.4,  "lon": -99.1,  "country": "MX", "baseline": 10 }
  }
}
```

> 新 Region 发布时，只需在此配置中添加一行。GPRI 引擎自动将七类信号按 country/坐标关联到对应 Region。

### C. WorldMonitor API 端点参考

| 需求 | WorldMonitor RPC | 端点 |
|------|-----------------|------|
| ACLED 冲突事件 | ConflictService.ListAcledEvents | /api/conflict/v1/[rpc] |
| UCDP 暴力事件 | ConflictService.ListUcdpEvents | /api/conflict/v1/[rpc] |
| 网络威胁 | CyberService.ListCyberThreats | /api/cyber/v1/[rpc] |
| 海缆健康 | InfrastructureService.GetCableHealth | /api/infrastructure/v1/[rpc] |
| 断网检测 | InfrastructureService.ListInternetOutages | /api/infrastructure/v1/[rpc] |
| 流量异常 | InfrastructureService.ListInternetTrafficAnomalies | /api/infrastructure/v1/[rpc] |
| DDoS 攻击 | InfrastructureService.ListInternetDdosAttacks | /api/infrastructure/v1/[rpc] |
| 气候异常 | ClimateService.ListClimateAnomalies | /api/climate/v1/[rpc] |
| 地震 | SeismologyService.ListEarthquakes | /api/seismology/v1/[rpc] |
| 制裁压力 | SanctionsService.ListSanctionsPressure | /api/sanctions/v1/[rpc] |
| 安全顾问 | IntelligenceService.ListSecurityAdvisories | /api/intelligence/v1/[rpc] |
| GDELT 新闻 | IntelligenceService.SearchGdeltDocuments | /api/intelligence/v1/[rpc] |
| 航行警告 | MaritimeService.ListNavigationalWarnings | /api/maritime/v1/[rpc] |
| 天气预报 | ForecastService.GetForecasts | /api/forecast/v1/[rpc] |
