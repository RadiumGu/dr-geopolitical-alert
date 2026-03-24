"""CloudWatch Dashboard for DR Geopolitical Alert."""
from aws_cdk import (
    Duration,
    aws_cloudwatch as cloudwatch,
)
from constructs import Construct

REGIONS = ["JP", "CN", "KR", "TW", "US", "RU", "EU"]
METRIC_NAMESPACE = "DrGeopoliticalAlert"


class DashboardConstruct(Construct):
    """CloudWatch Dashboard with GPRI score widgets per region."""

    def __init__(self, scope: Construct, id: str) -> None:
        super().__init__(scope, id)

        number_widgets = [
            cloudwatch.SingleValueWidget(
                title=f"GPRI Score — {region}",
                metrics=[
                    cloudwatch.Metric(
                        namespace=METRIC_NAMESPACE,
                        metric_name="GpriScore",
                        dimensions_map={"Region": region},
                        period=Duration.minutes(5),
                        statistic="Maximum",
                    )
                ],
                width=4,
                height=3,
            )
            for region in REGIONS
        ]

        trend_widgets = [
            cloudwatch.GraphWidget(
                title=f"GPRI Trend — {region}",
                left=[
                    cloudwatch.Metric(
                        namespace=METRIC_NAMESPACE,
                        metric_name="GpriScore",
                        dimensions_map={"Region": region},
                        period=Duration.minutes(5),
                        statistic="Maximum",
                        label=region,
                    )
                ],
                width=12,
                height=6,
                period=Duration.hours(24),
            )
            for region in REGIONS
        ]

        self.dashboard = cloudwatch.Dashboard(
            self,
            "DrGeopoliticalAlertDashboard",
            dashboard_name="DrGeopoliticalAlert",
            widgets=[
                number_widgets,
                trend_widgets[:2],
                trend_widgets[2:4],
                trend_widgets[4:6],
                [trend_widgets[6]],
            ],
        )
