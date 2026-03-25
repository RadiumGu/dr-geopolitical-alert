"""Signal collector Lambdas + EventBridge schedules.

API tokens are read at **runtime** from SSM Parameter Store by the Lambda code.
No tokens are injected at deploy time — just deploy and configure tokens in SSM:

    aws ssm put-parameter --name "/dr-alert/ucdp-access-token" \
        --value "<token>" --type String --region <REGION>

See README for the full list of supported SSM parameters.
"""
from aws_cdk import (
    Duration,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_sqs as sqs,
)
from constructs import Construct

COLLECTOR_NAMES = [
    "weather",
    "conflict",
    "cyber",
    "political",
    "infrastructure",
    "compliance",
    "bgp",
]

SIGNALS_TABLE_NAME = "dr-alert-signals"


class CollectorsConstruct(Construct):
    """Seven signal-collector Lambdas, each triggered every 10 minutes."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        signals_table: dynamodb.Table,
        dlq: sqs.Queue | None = None,
    ) -> None:
        super().__init__(scope, id)

        code = lambda_.Code.from_asset("src")

        # IAM policy for reading SSM parameters at runtime
        ssm_read_policy = iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[f"arn:aws:ssm:*:*:parameter/dr-alert/*"],
        )

        self.functions: dict[str, lambda_.Function] = {}

        for name in COLLECTOR_NAMES:
            kwargs = {}
            if dlq:
                kwargs["dead_letter_queue"] = dlq

            fn = lambda_.Function(
                self,
                f"{name.capitalize()}Collector",
                function_name=f"dr-alert-collector-{name}",
                runtime=lambda_.Runtime.PYTHON_3_13,
                architecture=lambda_.Architecture.ARM_64,
                handler=f"collectors.{name}.handler",
                code=code,
                memory_size=256,
                timeout=Duration.seconds(60),
                environment={
                    "SIGNALS_TABLE": SIGNALS_TABLE_NAME,
                },
                **kwargs,
            )

            signals_table.grant_read_write_data(fn)

            # Grant SSM read for runtime secret loading
            fn.add_to_role_policy(ssm_read_policy)

            events.Rule(
                self,
                f"{name.capitalize()}Schedule",
                schedule=events.Schedule.rate(Duration.minutes(10)),
                targets=[targets.LambdaFunction(fn)],
            )

            self.functions[name] = fn
