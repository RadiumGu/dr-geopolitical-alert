[English](README.md) | [中文](README_zh.md)

# DR Geopolitical Alert System

> **AWS Cross-Region Disaster Recovery Pre-Alert System — Geopolitical Risk Enhanced**

A serverless system that continuously monitors **34 AWS commercial Regions** across 7 risk dimensions, calculates a Geopolitical Risk Index (GPRI, 0–100), and triggers alerts when risk levels change — enabling proactive DR decisions **before** technical failures occur.

## Why?

Traditional DR monitoring only detects failures after they happen. This system adds a **predictive layer** by tracking geopolitical, environmental, and network signals that precede AWS Region disruptions:

- 🌊 A submarine cable cut near Bahrain → GPRI rises **hours before** latency increases
- 🌪️ A typhoon approaching Tokyo → GPRI rises **days before** potential AZ outages
- 🔒 New sanctions against a country → GPRI flags compliance risk for that Region

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Layer 0     │     │  Layer 1     │     │  Layer 2     │     │  Layer 3     │
│  Signal      │ ──▶ │  GPRI        │ ──▶ │  Adjudica-   │ ──▶ │  Action      │
│  Collectors  │     │  Calculator  │     │  tion Engine │     │  Triggers    │
│  (7 classes) │     │  (scoring)   │     │  (cross-val) │     │  (SNS/Slack) │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
       │                                                              │
       ▼                                                              ▼
  DynamoDB (signals)                                    CloudWatch Dashboard
  DynamoDB (gpri)                                       Slack Notifications
```

## Seven Signal Classes (A–G)

| Class | Dimension | Weight | Data Sources | Cadence |
|-------|-----------|--------|-------------|---------|
| **A** | Armed Conflict | 20 | UCDP GED → ACLED fallback | 10 min |
| **B** | Cyber Threats | 15 | abuse.ch (Feodo+URLhaus), trend-based | 10 min |
| **C** | Political Stability | 15 | US State Dept Travel Advisory RSS | 10 min |
| **D** | Physical Infrastructure | 10 | RIPE Atlas probe connectivity | 10 min |
| **E** | Extreme Weather | 15 | Open-Meteo (batch) + USGS + GDACS | 10 min |
| **F** | Compliance/Regulatory | 10 | OFAC RSS + EU Official Journal | 10 min |
| **G** | BGP/Backbone | 15 | IODA (Internet Outage Detection) | 10 min |

## GPRI Scoring

```
GPRI = Baseline + Σ(Signal_i × Weight_i)    capped at 100
```

### Baseline Score

Each Region has a **static baseline** (0–25) reflecting inherent geopolitical risk, plus a **dynamic delta** (±5) that adjusts weekly based on observed signal trends.

```
effective_baseline = static_baseline + dynamic_delta
```

#### Static Baseline

Baselines are pre-assigned based on 5 factors:

| Factor | Examples |
|--------|----------|
| Geopolitical tension | Active conflicts, territorial disputes, international sanctions |
| Cyber sovereignty risk | Internet censorship, cross-border data regulations |
| Infrastructure fragility | Power grid stability, submarine cable dependency |
| Natural disaster exposure | Seismic zones, typhoon/hurricane paths, flood risk |
| Legal/compliance complexity | Data protection laws, export controls |

**Baseline by Region** (sorted high → low):

| Baseline | Regions |
|----------|---------|
| 25 | 🇮🇱 il-central-1 (Tel Aviv) |
| 20 | 🇦🇪 me-central-1 (Dubai) |
| 18 | 🇧🇭 me-south-1 (Bahrain) |
| 15 | 🇿🇦 af-south-1 (Cape Town) |
| 12 | 🇭🇰 ap-east-1 (Hong Kong) |
| 10 | 🇰🇷 ap-east-2, 🇮🇳 ap-south-1/2, 🇮🇩 ap-southeast-3, 🇧🇷 sa-east-1, 🇲🇽 mx-central-1 |
| 8–9 | 🇹🇭 ap-southeast-6, 🇦🇺 ap-southeast-4, 🇳🇿 ap-southeast-5, 🇲🇾 ap-southeast-7, 🇮🇹 eu-south-1, 🇪🇸 eu-south-2 |
| 5–6 | 🇯🇵 ap-northeast-1/3, 🇰🇷 ap-northeast-2 |
| 2–4 | 🇺🇸 us-east-1/2, us-west-1/2, 🇨🇦 ca-central-1, ca-west-1, 🇩🇪 eu-central-1, 🇬🇧 eu-west-2, 🇫🇷 eu-west-3, 🇸🇪 eu-north-1, 🇦🇺 ap-southeast-2, 🇨🇭 eu-central-2, 🇮🇪 eu-west-1, 🇸🇬 ap-southeast-1 |

#### Dynamic Baseline Calibration

A weekly calibration Lambda (`dr-alert-baseline-calibrator`) automatically adjusts each Region's baseline by computing a **dynamic delta** based on 30-day signal history:

1. **Every Sunday 00:00 UTC**, for each Region:
   - Query the last 30 days of signal scores across all 7 dimensions (A–G)
   - Compute the **median** score per dimension (robust against outliers), then sum them
   - Calculate: `deviation = signal_median_sum - static_baseline`
   - Apply damping: `delta = clamp(round(deviation × 0.3), -5, +5)`
   - Store delta in DynamoDB (`CONFIG#baseline_delta`)

2. **The GPRI calculator** reads the delta every 5-minute cycle:
   ```
   effective_baseline = static_baseline + delta
   GPRI = effective_baseline + Σ(signals)
   ```

**Design constraints:**

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Frequency | Weekly (Sunday 00:00 UTC) | Geopolitical shifts are slow variables; weekly prevents noise-driven drift |
| Lookback | 30 days (~4,300 samples/region) | Statistically robust, captures sustained trends |
| Damping | 0.3 | A 10-point deviation only moves delta by 3 — smooth transitions |
| Max delta | ±5 | Tel Aviv (25) can't drift to Oregon (2) levels |
| Aggregation | Median (not mean) | A single extreme event won't skew the baseline |
| Min samples | 50 | Below this, calibration is skipped (keeps existing delta) |
| SNS notification | On delta change only | Auditable; no spam when baselines are stable |

### Risk Levels

| Level | Range | Color | Action |
|-------|-------|-------|--------|
| GREEN | 0–30 | 🟢 | Normal operations |
| YELLOW | 31–50 | 🟡 | Increase monitoring, review DR readiness |
| ORANGE | 51–70 | 🟠 | Scale up standby Region, lower TTL |
| RED | 71–85 | 🔴 | Initiate DR switchover decision process |
| BLACK | 86–100 | ⚫ | Execute DR switchover immediately |

### Adjudication (Cross-Validation)

Single-signal dominance is **downgraded** (LOW confidence). Multiple corroborating signals are **upgraded** (HIGH confidence). This prevents false alarms from noisy single-source data.

## Deployment

### Prerequisites

- AWS Account with CDK bootstrapped in `us-west-2`
- Python 3.12+
- AWS CDK CLI

### Deploy

```bash
cd dr-geopolitical-alert
pip install -r requirements.txt

# Deploy (uses your default AWS account, region defaults to us-west-2)
cdk deploy

# Or specify a different region
CDK_DEPLOY_REGION=eu-west-1 cdk deploy
```

The stack deploys to **us-west-2 (Oregon)** — independent from monitored Regions for control-plane isolation.

### Post-Deploy: Configure API Tokens (Optional)

API tokens are loaded **at runtime** from SSM Parameter Store — no redeployment needed when adding or changing tokens. Just set them whenever you have them:

```bash
REGION=us-west-2  # your deploy region

# Slack webhook for alert notifications
aws ssm put-parameter --name "/dr-alert/slack-webhook-url" \
    --value "https://hooks.slack.com/services/YOUR/WEBHOOK/URL" \
    --type String --region $REGION --overwrite

# UCDP token — A-class armed conflict data (email mertcan.yilmaz@pcr.uu.se to get one)
aws ssm put-parameter --name "/dr-alert/ucdp-access-token" \
    --value "<your-ucdp-token>" --type String --region $REGION --overwrite

# ACLED credentials — A-class fallback (register at developer.acleddata.com)
aws ssm put-parameter --name "/dr-alert/acled-api-key" \
    --value "<your-acled-key>" --type String --region $REGION --overwrite
aws ssm put-parameter --name "/dr-alert/acled-email" \
    --value "<your-email>" --type String --region $REGION --overwrite

# Cloudflare Radar — G-class DDoS/BGP leak detection (free Cloudflare account)
aws ssm put-parameter --name "/dr-alert/cf-radar-token" \
    --value "<your-cf-token>" --type String --region $REGION --overwrite
```

> **No redeployment needed.** Tokens are read from SSM on each Lambda invocation (cached per container). Add tokens at any time — they take effect within minutes.

### Manual Trigger to Verify

```bash
aws lambda invoke --function-name dr-alert-collector-weather --region us-west-2 /tmp/out.json
aws lambda invoke --function-name dr-alert-gpri-calculator --region us-west-2 /tmp/out.json
```

## GPRI Query API

A read-only API to query live GPRI scores.

> **⚠️ Security Note:** The public HTTP endpoint (Lambda Function URL) is **disabled by default**. The Lambda function is always deployed for internal/SDK invocation. To enable public HTTP access:
>
> ```bash
> cdk deploy -c enable_api_url=true
> ```
>
> The Function URL will be output as `DrGeopoliticalAlertStack.ApiGpriQueryUrl`.

### Query a single Region

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

### Query all 34 Regions

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

> The Function URL is output by `cdk deploy -c enable_api_url=true` as `DrGeopoliticalAlertStack.ApiGpriQueryUrl`. Without this flag, the Lambda exists but has no public endpoint — invoke it via AWS SDK or CLI only.

## AWS Resources

| Resource | Count | Purpose |
|----------|-------|---------|
| Lambda Functions | 11 | 7 collectors + 1 GPRI engine + 1 baseline calibrator + 1 Slack notifier + 1 API query |
| DynamoDB Tables | 2 | `dr-alert-signals` + `dr-alert-gpri` |
| EventBridge Rules | 9 | 7 × 10min (collectors) + 1 × 5min (GPRI) + 1 weekly (calibrator) |
| SNS Topic | 1 | GPRI level change alerts |
| SQS Queue | 1 | Dead Letter Queue for failed invocations |
| CloudWatch Dashboard | 1 | 39 widgets, all 34 Regions |
| CloudWatch Alarm | 1 | DLQ depth > 0 |

**Estimated monthly cost: $5–15** (all serverless, pay-per-use)

### Cost Breakdown

| Resource | Estimate | Notes |
|----------|----------|-------|
| Lambda | ~$2–5 | 9 functions × ~4,300 invocations/month (every 5–10 min), 256MB, <3s avg |
| DynamoDB | ~$1–3 | On-demand mode; ~4,300 writes/month per collector + 34 GPRI writes per cycle |
| EventBridge | Free | Included in free tier (8 rules) |
| CloudWatch Dashboard | $3 | 1 custom dashboard |
| SNS/SQS | ~$0 | Minimal usage (only on level changes) |
| CloudWatch Alarm | ~$0.10 | 1 alarm |
| **Total** | **~$6–11/month** | No NAT Gateway, no VPC, no reserved capacity |

> Most external data sources are **free public APIs**. Some require API tokens for full functionality — see the Data Sources section below.

## Data Sources

### Free — No Authentication Required

| Class | Source | API Endpoint | What It Provides | Status |
|-------|--------|-------------|-----------------|--------|
| **B** | [abuse.ch Feodo Tracker](https://feodotracker.abuse.ch/) | `feodotracker.abuse.ch` | Botnet C2 IP blocklist | ✅ Working |
| **C** | [US State Dept Travel Advisory](https://travel.state.gov/) | `travel.state.gov` RSS | Country travel risk levels (1–4) | ✅ Working |
| **D** | [RIPE Atlas](https://atlas.ripe.net/) | `atlas.ripe.net/api/v2/probes/` | Network probe connectivity by country | ✅ Working |
| **E** | [Open-Meteo](https://open-meteo.com/) | `api.open-meteo.com/v1/forecast` | Extreme weather alerts (batch API) | ✅ Working |
| **E** | [USGS Earthquake](https://earthquake.usgs.gov/) | `earthquake.usgs.gov` GeoJSON | Significant seismic events | ✅ Working |
| **E** | [GDACS](https://www.gdacs.org/) (UN) | `gdacs.org/xml/rss.xml` | Global disaster alerts (flood, cyclone, volcano) | ✅ Working |
| **F** | [EU Official Journal](https://eur-lex.europa.eu/) | `eur-lex.europa.eu` RSS | EU regulatory/sanctions changes | ✅ Working |
| **G** | [IODA](https://ioda.inetintel.cc.gatech.edu/) (Georgia Tech) | `api.ioda.inetintel.cc.gatech.edu` | Internet outage detection (BGP, Active Probing, Darknet) | ✅ Working |

### Requires API Token (Free Registration)

| Class | Source | How to Get Token | Env Variable | Impact if Missing |
|-------|--------|-----------------|-------------|-------------------|
| **A** | [UCDP GED](https://ucdp.uu.se/) | Email `mertcan.yilmaz@pcr.uu.se` ([details](https://ucdp.uu.se/apidocs/)) | `UCDP_ACCESS_TOKEN` | ⚠️ **A-class blind** — no armed conflict data (0–20 points missing) |
| **A** | [ACLED](https://acleddata.com/) (fallback) | Register at [developer.acleddata.com](https://developer.acleddata.com/) | `ACLED_API_KEY` + `ACLED_EMAIL` | Backup for UCDP; more granular conflict data |
| **G** | [Cloudflare Radar](https://radar.cloudflare.com/) | Free [Cloudflare account](https://developers.cloudflare.com/radar/) | `CF_RADAR_TOKEN` | Optional — adds DDoS + traffic anomaly + BGP leak detection; IODA covers the basics |

### Known Issues

| Class | Source | Issue |
|-------|--------|-------|
| **B** | [abuse.ch URLhaus](https://urlhaus.abuse.ch/) | API returning 401; may need endpoint migration |
| **F** | [OFAC SDN](https://ofac.treasury.gov/) | Connection timeout from non-US regions; possible geo-blocking |

## Project Structure

```
dr-geopolitical-alert/
├── infra/                   # CDK Infrastructure (Python)
│   ├── app.py              # CDK entry point
│   ├── stacks/alert_stack.py
│   └── constructs_/
│       ├── tables.py        # DynamoDB tables
│       ├── collectors.py    # 7 Lambda + EventBridge
│       ├── gpri_engine.py   # GPRI calculator Lambda
│       ├── baseline_calibrator.py  # Weekly baseline calibration Lambda
│       ├── notification.py  # SNS + Slack Lambda
│       ├── dashboard.py     # CloudWatch Dashboard
│       └── api.py           # GPRI Query Lambda Function URL
├── src/                     # Lambda source code
│   ├── api/
│   │   └── gpri_query.py    # Public GPRI query endpoint
│   ├── collectors/          # 7 signal collectors (A–G)
│   ├── engine/
│   │   ├── gpri_calculator.py
│   │   ├── baseline_calibrator.py  # Weekly dynamic baseline calibration
│   │   └── adjudication.py  # Multi-signal cross-validation
│   ├── notify/
│   │   └── slack_dispatcher.py
│   └── shared/
│       ├── types.py         # Data models + enums
│       ├── region_config.py # 34 Region definitions + baselines
│       ├── db.py            # DynamoDB operations
│       └── http_client.py   # Resilient HTTP client
├── tests/unit/              # 100 unit tests
├── cdk.json
├── requirements.txt
└── conftest.py
```

## Testing

```bash
python3 -m pytest tests/ -v
# 100 passed
```

## Monitoring

**CloudWatch Dashboard**: `DrGeopoliticalAlert` in us-west-2

The dashboard has 39 widgets organized as follows:

- **Header**: `GPRI Total = Baseline (BL) + Real-Time Signals (A-G)` with color-coded level legend
- **34 Single-Value Widgets**: One per Region, sorted by baseline risk (highest first). Each shows:
  - Live GPRI total score (Baseline + Signals)
  - Sparkline trend
  - Title includes `BL:xx` showing the Region's static baseline score
  - Tier emoji: 🔴 high baseline (≥15), 🟡 medium (≥10), 🔵 moderate (≥6), 🟢 low (<6)
- **Timeline Graph**: `GPRI Total Score (Baseline + Signals) — Top 10 Risk Regions` with horizontal threshold lines at YELLOW/ORANGE/RED/BLACK
- **3 Signal Breakdown Charts**: `Real-Time Signals Only (excl. Baseline)` for the top 3 risk Regions (Israel, Bahrain, Dubai). Stacked area chart showing all 7 signal classes (A–G)

> **Note**: The GPRI Total (top widgets) = Baseline + Signals. The Signal Breakdown charts (bottom) show **only the real-time signal components**, excluding the static baseline. The difference between the two always equals the Region's baseline score (`BL:xx` in the title).

## Design Decisions

- **Deploy Region us-west-2**: Independent from monitored Regions; lowest baseline risk; closest to data sources (ACLED, OFAC, IODA servers in North America/Europe)
- **No VPC**: All Lambda functions access public APIs + DynamoDB endpoints directly; faster cold starts, no NAT Gateway cost
- **Adjudication engine**: Prevents single-signal false alarms (PRD §5.3 "multi-signal cross-validation")
- **D-class via RIPE Atlas**: Replaced unreliable GDELT news search with real network telemetry (probe connectivity ratios)
- **B-class trend comparison**: Changed from absolute counts (US always high) to anomaly-ratio vs historical baseline

## Disclaimer

This project is provided **for educational and reference purposes only**. The GPRI scores, baseline risk assessments, and signal analyses are based on publicly available data and simplified heuristics — they do **not** constitute professional geopolitical, security, or disaster recovery advice. Baseline scores reflect general country-level risk profiles and may not accurately represent conditions at specific AWS data center locations. Always conduct your own due diligence and consult qualified professionals before making critical infrastructure or DR decisions based on this system's output. The authors assume no liability for actions taken based on GPRI scores or alerts.

## License

MIT
