"""Weekly baseline calibrator Lambda + EventBridge cron schedule."""
from aws_cdk import (
    Duration,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_lambda as lambda_,
    aws_sns as sns,
    aws_sqs as sqs,
)
from constructs import Construct

SIGNALS_TABLE_NAME = "dr-alert-signals"
GPRI_TABLE_NAME = "dr-alert-gpri"


class BaselineCalibratorConstruct(Construct):
    """Weekly baseline calibration Lambda — runs every Sunday 00:00 UTC."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        signals_table: dynamodb.Table,
        gpri_table: dynamodb.Table,
        sns_topic: sns.Topic,
        dlq: sqs.Queue | None = None,
        layer: lambda_.LayerVersion | None = None,
    ) -> None:
        super().__init__(scope, id)

        kwargs = {}
        if dlq:
            kwargs["dead_letter_queue"] = dlq

        self.fn = lambda_.Function(
            self,
            "BaselineCalibrator",
            function_name="dr-alert-baseline-calibrator",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="engine.baseline_calibrator.handler",
            code=lambda_.Code.from_asset("src"),
            layers=[layer] if layer else [],
            memory_size=256,
            timeout=Duration.seconds(300),
            environment={
                "SIGNALS_TABLE": SIGNALS_TABLE_NAME,
                "GPRI_TABLE": GPRI_TABLE_NAME,
                "SNS_TOPIC_ARN": sns_topic.topic_arn,
            },
            **kwargs,
        )

        signals_table.grant_read_data(self.fn)
        gpri_table.grant_read_write_data(self.fn)
        sns_topic.grant_publish(self.fn)

        # Weekly schedule: every Sunday at 00:00 UTC
        events.Rule(
            self,
            "WeeklyCalibrationSchedule",
            schedule=events.Schedule.cron(
                minute="0", hour="0", week_day="SUN",
            ),
            targets=[targets.LambdaFunction(self.fn)],
        )
