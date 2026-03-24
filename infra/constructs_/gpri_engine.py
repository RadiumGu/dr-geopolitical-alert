"""GPRI calculation engine Lambda + EventBridge schedule."""
from aws_cdk import (
    Duration,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_sns as sns,
    aws_sqs as sqs,
)
from constructs import Construct

SIGNALS_TABLE_NAME = "dr-alert-signals"
GPRI_TABLE_NAME = "dr-alert-gpri"


class GpriEngineConstruct(Construct):
    """GPRI calculator Lambda triggered every 5 minutes."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        signals_table: dynamodb.Table,
        gpri_table: dynamodb.Table,
        sns_topic: sns.Topic,
        dlq: sqs.Queue | None = None,
    ) -> None:
        super().__init__(scope, id)

        kwargs = {}
        if dlq:
            kwargs["dead_letter_queue"] = dlq

        self.fn = lambda_.Function(
            self,
            "GpriCalculator",
            function_name="dr-alert-gpri-calculator",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="engine.gpri_calculator.handler",
            code=lambda_.Code.from_asset("src"),
            memory_size=256,
            timeout=Duration.seconds(30),
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

        self.fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
            )
        )

        events.Rule(
            self,
            "GpriSchedule",
            schedule=events.Schedule.rate(Duration.minutes(5)),
            targets=[targets.LambdaFunction(self.fn)],
        )
