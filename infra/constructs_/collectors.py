"""Signal collector Lambdas + EventBridge schedules.

API tokens are read from SSM Parameter Store (String type).
Before deploying, create the required parameters:

    # Required — A-class armed conflict data
    aws ssm put-parameter --name "/dr-alert/ucdp-access-token" \
        --value "<your-token>" --type String --region <REGION>

    # Optional — A-class fallback (ACLED)
    aws ssm put-parameter --name "/dr-alert/acled-api-key" \
        --value "<your-key>" --type String --region <REGION>
    aws ssm put-parameter --name "/dr-alert/acled-email" \
        --value "<your-email>" --type String --region <REGION>

    # Optional — G-class Cloudflare Radar enhancement
    aws ssm put-parameter --name "/dr-alert/cf-radar-token" \
        --value "<your-token>" --type String --region <REGION>
"""
from aws_cdk import (
    Duration,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_lambda as lambda_,
    aws_sqs as sqs,
    aws_ssm as ssm,
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

# SSM parameters that must exist before deploy (looked up at synth time)
_SSM_REQUIRED = [
    ("/dr-alert/ucdp-access-token", "UCDP_ACCESS_TOKEN", "conflict"),
    ("/dr-alert/cf-radar-token",    "CF_RADAR_TOKEN",     "bgp"),
]

# Optional tokens passed via CDK context: cdk deploy -c acled_api_key=xxx -c acled_email=xxx
_CONTEXT_OPTIONAL = [
    ("acled_api_key", "ACLED_API_KEY", "conflict"),
    ("acled_email",   "ACLED_EMAIL",   "conflict"),
]


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

        self.functions: dict[str, lambda_.Function] = {}

        for name in COLLECTOR_NAMES:
            kwargs = {}
            if dlq:
                kwargs["dead_letter_queue"] = dlq

            env = {"SIGNALS_TABLE": SIGNALS_TABLE_NAME}

            # Inject required SSM tokens
            for ssm_path, env_var, target_collector in _SSM_REQUIRED:
                if target_collector == name:
                    env[env_var] = ssm.StringParameter.value_from_lookup(
                        self, ssm_path
                    )

            # Inject optional context tokens
            for ctx_key, env_var, target_collector in _CONTEXT_OPTIONAL:
                if target_collector == name:
                    val = self.node.try_get_context(ctx_key)
                    if val:
                        env[env_var] = val

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
                environment=env,
                **kwargs,
            )

            signals_table.grant_read_write_data(fn)

            events.Rule(
                self,
                f"{name.capitalize()}Schedule",
                schedule=events.Schedule.rate(Duration.minutes(10)),
                targets=[targets.LambdaFunction(fn)],
            )

            self.functions[name] = fn
