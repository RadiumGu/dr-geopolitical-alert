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
    """CloudWatch Dashboard covering all 34 Regions with GPRI gauges and A-G signal breakdowns."""

    def __init__(self, scope: Construct, id: str) -> None:
        super().__init__(scope, id)

        regions_sorted = sorted(ALL_REGIONS, key=lambda x: -x.baseline)

        signal_classes = [
            ("A", "A"), ("B", "B"), ("C", "C"), ("D", "D"),
            ("E", "E"), ("F", "F"), ("G", "G"),
        ]

        # --- Gauge widgets (GPRI total per region, color-coded) ---
        gauge_widgets = []
        for r in regions_sorted:
            gauge_widgets.append(
                cloudwatch.GaugeWidget(
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
                    left_y_axis=cloudwatch.YAxisProps(min=0, max=100),
                    annotations=[
                        cloudwatch.HorizontalAnnotation(
                            value=0, label="GREEN", color="#2ca02c",
                            fill=cloudwatch.Shading.ABOVE,
                        ),
                        cloudwatch.HorizontalAnnotation(
                            value=31, label="YELLOW", color="#f5c542",
                            fill=cloudwatch.Shading.ABOVE,
                        ),
                        cloudwatch.HorizontalAnnotation(
                            value=51, label="ORANGE", color="#f59c42",
                            fill=cloudwatch.Shading.ABOVE,
                        ),
                        cloudwatch.HorizontalAnnotation(
                            value=71, label="RED", color="#d13212",
                            fill=cloudwatch.Shading.ABOVE,
                        ),
                        cloudwatch.HorizontalAnnotation(
                            value=86, label="BLACK", color="#1d1d1d",
                            fill=cloudwatch.Shading.ABOVE,
                        ),
                    ],
                    width=4,
                    height=5,
                )
            )

        # --- Per-region signal class breakdown (A-G) — bar chart view ---
        signal_svw_widgets = []
        for r in regions_sorted:
            signal_svw_widgets.append(
                cloudwatch.GraphWidget(
                    title=f"{r.code} Signals A-G",
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
                    view=cloudwatch.GraphWidgetView.BAR,
                    width=4,
                    height=4,
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

        # --- Signal breakdown: top 3 stacked graph (detail view) ---
        top3_breakdown_widgets = []
        top3_signal_classes = [
            ("A", "Conflict"), ("B", "Cyber"), ("C", "Political"),
            ("D", "Infra"), ("E", "Weather"), ("F", "Compliance"), ("G", "BGP"),
        ]
        for r in regions_sorted[:3]:
            top3_breakdown_widgets.append(
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
                        for cls, label in top3_signal_classes
                    ],
                    stacked=True,
                    width=8,
                    height=6,
                )
            )

        # --- Assemble dashboard ---
        # Interleave gauge rows and signal-breakdown rows (6 regions per row)
        region_rows: list[list] = []
        for i in range(0, len(gauge_widgets), 6):
            region_rows.append(gauge_widgets[i:i + 6])
            region_rows.append(signal_svw_widgets[i:i + 6])

        self.dashboard = cloudwatch.Dashboard(
            self,
            "DrGeopoliticalAlertDashboard",
            dashboard_name="DrGeopoliticalAlert",
            widgets=[
                # Header row
                [cloudwatch.TextWidget(
                    markdown=(
                        "# 🌍 DR Geopolitical Risk Index (GPRI) — 34 Regions\n\n"
                        + "GPRI Total = Baseline (BL) + Real-Time Signals (A-G) &nbsp;|&nbsp; "
                        + "⚫ BLACK(86-100) | 🔴 RED(71-85) | 🟠 ORANGE(51-70) | 🟡 YELLOW(31-50) | 🟢 GREEN(0-30)\n\n"
                        + "Signals: **A** Conflict | **B** Cyber | **C** Political | **D** Infra | **E** Weather | **F** Compliance | **G** BGP"
                    ),
                    width=24,
                    height=3,
                )],
                *region_rows,
                [timeline],
                top3_breakdown_widgets,
            ],
        )
