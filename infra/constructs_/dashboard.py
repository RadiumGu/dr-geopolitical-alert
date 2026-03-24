"""CloudWatch Dashboard for DR Geopolitical Alert.

Dashboard layout is generated programmatically from region_config to ensure
all 34 Regions are included and sorted by baseline risk.
The same JSON is saved to infra/dashboard_body.json for reference.
"""
import json
import sys
from pathlib import Path

from aws_cdk import (
    Duration,
    aws_cloudwatch as cloudwatch,
)
from constructs import Construct

# Add src/ to path so we can import region_config
_src = str(Path(__file__).resolve().parent.parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from shared.region_config import ALL_REGIONS


NS = "DrAlert/GPRI"


class DashboardConstruct(Construct):
    """CloudWatch Dashboard with 39 widgets covering all 34 Regions."""

    def __init__(self, scope: Construct, id: str) -> None:
        super().__init__(scope, id)

        regions_sorted = sorted(ALL_REGIONS, key=lambda x: -x.baseline)

        # --- Single-value widgets (GPRI total per region) ---
        sv_widgets = []
        for r in regions_sorted:
            sv_widgets.append(
                cloudwatch.SingleValueWidget(
                    title=f"{r.code} ({r.city}) BL:{r.baseline}",
                    metrics=[
                        cloudwatch.Metric(
                            namespace=NS,
                            metric_name="Score",
                            dimensions_map={"Region": r.code},
                            period=Duration.minutes(5),
                            statistic="Maximum",
                        )
                    ],
                    sparkline=True,
                    width=4,
                    height=3,
                )
            )

        # --- Timeline graph: top 10 by baseline ---
        top10 = regions_sorted[:10]
        timeline = cloudwatch.GraphWidget(
            title="GPRI Total Score (Baseline + Signals) — Top 10 Risk Regions",
            left=[
                cloudwatch.Metric(
                    namespace=NS,
                    metric_name="Score",
                    dimensions_map={"Region": r.code},
                    period=Duration.minutes(5),
                    statistic="Maximum",
                    label=f"{r.code} ({r.city})",
                )
                for r in top10
            ],
            width=24,
            height=6,
            left_y_axis=cloudwatch.YAxisProps(min=0, max=100),
            left_annotations=[
                cloudwatch.HorizontalAnnotation(value=31, label="YELLOW", color="#f5c542"),
                cloudwatch.HorizontalAnnotation(value=51, label="ORANGE", color="#f59c42"),
                cloudwatch.HorizontalAnnotation(value=71, label="RED", color="#d13212"),
                cloudwatch.HorizontalAnnotation(value=86, label="BLACK", color="#1d1d1d"),
            ],
        )

        # --- Signal breakdown: top 3 ---
        signal_classes = [
            ("A", "Conflict"), ("B", "Cyber"), ("C", "Political"),
            ("D", "Infra"), ("E", "Weather"), ("F", "Compliance"), ("G", "BGP"),
        ]
        breakdown_widgets = []
        for r in regions_sorted[:3]:
            breakdown_widgets.append(
                cloudwatch.GraphWidget(
                    title=f"Real-Time Signals Only (excl. Baseline) — {r.code} ({r.city})",
                    left=[
                        cloudwatch.Metric(
                            namespace=NS,
                            metric_name="SignalScore",
                            dimensions_map={"Region": r.code, "Class": cls},
                            period=Duration.minutes(5),
                            statistic="Maximum",
                            label=label,
                        )
                        for cls, label in signal_classes
                    ],
                    stacked=True,
                    width=8,
                    height=6,
                )
            )

        # --- Assemble dashboard ---
        # Group single-value widgets into rows of 6
        sv_rows = []
        for i in range(0, len(sv_widgets), 6):
            sv_rows.append(sv_widgets[i:i + 6])

        self.dashboard = cloudwatch.Dashboard(
            self,
            "DrGeopoliticalAlertDashboard",
            dashboard_name="DrGeopoliticalAlert",
            widgets=[
                # Header row
                [cloudwatch.TextWidget(
                    markdown="# 🌍 DR Geopolitical Risk Index (GPRI) — 34 Regions\n"
                             "GPRI Total = Baseline (BL) + Real-Time Signals (A-G) &nbsp;|&nbsp; "
                             "⚫ BLACK(86-100) | 🔴 RED(71-85) | 🟠 ORANGE(51-70) | 🟡 YELLOW(31-50) | 🟢 GREEN(0-30)",
                    width=24,
                    height=2,
                )],
                *sv_rows,
                [timeline],
                breakdown_widgets,
            ],
        )
