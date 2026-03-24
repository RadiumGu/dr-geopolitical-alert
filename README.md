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

Each of the 34 Regions has a **static baseline** reflecting inherent geopolitical risk (e.g., Israel=25, Singapore=2). Real-time signals add to this baseline.

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
cdk deploy
```

The stack deploys to **us-west-2 (Oregon)** — independent from monitored Regions for control-plane isolation.

### Post-Deploy

```bash
# Set Slack webhook for notifications
aws ssm put-parameter \
  --name "/dr-alert/slack-webhook-url" \
  --value "https://hooks.slack.com/services/YOUR/WEBHOOK/URL" \
  --type String --region us-west-2 --overwrite

# Manual trigger to verify
aws lambda invoke --function-name dr-alert-collector-weather --region us-west-2 /tmp/out.json
aws lambda invoke --function-name dr-alert-gpri-calculator --region us-west-2 /tmp/out.json
```

## AWS Resources

| Resource | Count | Purpose |
|----------|-------|---------|
| Lambda Functions | 9 | 7 collectors + 1 GPRI engine + 1 Slack notifier |
| DynamoDB Tables | 2 | `dr-alert-signals` + `dr-alert-gpri` |
| EventBridge Rules | 8 | 7 × 10min (collectors) + 1 × 5min (GPRI) |
| SNS Topic | 1 | GPRI level change alerts |
| SQS Queue | 1 | Dead Letter Queue for failed invocations |
| CloudWatch Dashboard | 1 | 39 widgets, all 34 Regions |
| CloudWatch Alarm | 1 | DLQ depth > 0 |

**Estimated monthly cost: $5–15** (all serverless, pay-per-use)

## Project Structure

```
dr-geopolitical-alert/
├── doc/
│   ├── prd.md              # Product Requirements (755 lines, 34 Region profiles)
│   ├── tdd.md              # Technical Design Document
│   └── review.md           # Architecture review report
├── infra/                   # CDK Infrastructure (Python)
│   ├── app.py              # CDK entry point
│   ├── stacks/alert_stack.py
│   └── constructs_/
│       ├── tables.py        # DynamoDB tables
│       ├── collectors.py    # 7 Lambda + EventBridge
│       ├── gpri_engine.py   # GPRI calculator Lambda
│       ├── notification.py  # SNS + Slack Lambda
│       └── dashboard.py     # CloudWatch Dashboard
├── src/                     # Lambda source code
│   ├── collectors/          # 7 signal collectors (A–G)
│   ├── engine/
│   │   ├── gpri_calculator.py
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
- 34 single-value widgets sorted by baseline risk (highest first)
- Top 10 risk timeline with threshold annotations
- Signal breakdown charts for top 3 risk Regions

## Design Decisions

- **Deploy Region us-west-2**: Independent from monitored Regions; lowest baseline risk; closest to data sources (ACLED, OFAC, IODA servers in North America/Europe)
- **No VPC**: All Lambda functions access public APIs + DynamoDB endpoints directly; faster cold starts, no NAT Gateway cost
- **Adjudication engine**: Prevents single-signal false alarms (PRD §5.3 "multi-signal cross-validation")
- **D-class via RIPE Atlas**: Replaced unreliable GDELT news search with real network telemetry (probe connectivity ratios)
- **B-class trend comparison**: Changed from absolute counts (US always high) to anomaly-ratio vs historical baseline

## License

MIT
