"""Main CDK Stack — assembles all constructs."""
from aws_cdk import (
    BundlingOptions,
    Duration,
    Stack,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_lambda as lambda_,
    aws_sqs as sqs,
)
from constructs import Construct
from constructs_.tables import TablesConstruct
from constructs_.collectors import CollectorsConstruct
from constructs_.gpri_engine import GpriEngineConstruct
from constructs_.notification import NotificationConstruct
from constructs_.dashboard import DashboardConstruct
from constructs_.api import ApiConstruct
from constructs_.baseline_calibrator import BaselineCalibratorConstruct


class DrGeopoliticalAlertStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # 0. Shared Lambda Layer for third-party dependencies
        deps_layer = lambda_.LayerVersion(
            self,
            "DependenciesLayer",
            layer_version_name="dr-alert-dependencies",
            code=lambda_.Code.from_asset(
                "layers/dependencies",
                bundling=BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_13.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        "pip install -r requirements.txt -t /asset-output/python",
                    ],
                ),
            ),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_13],
            compatible_architectures=[lambda_.Architecture.ARM_64],
            description="Third-party dependencies: requests, feedparser",
        )

        # 1. Shared Dead Letter Queue for all Lambdas
        dlq = sqs.Queue(
            self,
            "DeadLetterQueue",
            queue_name="dr-alert-dlq",
            retention_period=Duration.days(14),
        )

        # 2. DynamoDB tables
        tables = TablesConstruct(self, "Tables")

        # 3. Notification (SNS + Slack) — created before engine so topic is available
        notification = NotificationConstruct(self, "Notification", dlq=dlq, layer=deps_layer)

        # 4. GPRI Engine
        engine = GpriEngineConstruct(
            self,
            "Engine",
            signals_table=tables.signals_table,
            gpri_table=tables.gpri_table,
            sns_topic=notification.topic,
            dlq=dlq,
            layer=deps_layer,
        )

        # 5. Signal Collectors
        collectors = CollectorsConstruct(
            self,
            "Collectors",
            signals_table=tables.signals_table,
            dlq=dlq,
            layer=deps_layer,
        )

        # 6. CloudWatch Dashboard
        dashboard = DashboardConstruct(self, "Dashboard")

        # 7. Public GPRI Query API (Lambda Function URL)
        api = ApiConstruct(self, "Api", gpri_table=tables.gpri_table, layer=deps_layer)

        # 8. Weekly Baseline Calibrator
        calibrator = BaselineCalibratorConstruct(
            self,
            "Calibrator",
            signals_table=tables.signals_table,
            gpri_table=tables.gpri_table,
            sns_topic=notification.topic,
            dlq=dlq,
            layer=deps_layer,
        )

        # 9. DLQ Alarm — fires when any Lambda sends a failed event to DLQ
        dlq_alarm = cloudwatch.Alarm(
            self,
            "DlqAlarm",
            alarm_name="dr-alert-dlq-not-empty",
            metric=dlq.metric_approximate_number_of_messages_visible(
                period=Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            alarm_description="DR Alert: Dead Letter Queue has failed Lambda invocations",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        dlq_alarm.add_alarm_action(cw_actions.SnsAction(notification.topic))
