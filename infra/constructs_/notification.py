"""SNS Topic + Slack dispatcher Lambda subscription."""
from aws_cdk import (
    Duration,
    Stack,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_sns as sns,
    aws_sns_subscriptions as sns_subscriptions,
    aws_sqs as sqs,
)
from constructs import Construct

SLACK_WEBHOOK_SSM_PATH = "/dr-alert/slack-webhook-url"


class NotificationConstruct(Construct):
    """SNS topic for GPRI level changes + Slack dispatcher Lambda."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        dlq: sqs.Queue | None = None,
        layer: lambda_.LayerVersion | None = None,
    ) -> None:
        super().__init__(scope, id)

        self.topic = sns.Topic(
            self,
            "AlertTopic",
            topic_name="dr-alert-gpri-changes",
            display_name="DR Geopolitical Alert — GPRI Level Changes",
        )

        # Pass SSM path as env var; Lambda reads at runtime (no synth-time resolve)
        env = {"SLACK_WEBHOOK_SSM_PATH": SLACK_WEBHOOK_SSM_PATH}

        kwargs = {}
        if dlq:
            kwargs["dead_letter_queue"] = dlq

        slack_fn = lambda_.Function(
            self,
            "SlackDispatcher",
            function_name="dr-alert-slack-dispatcher",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="notify.slack_dispatcher.handler",
            code=lambda_.Code.from_asset("src"),
            layers=[layer] if layer else [],
            memory_size=256,
            timeout=Duration.seconds(30),
            environment=env,
            **kwargs,
        )

        # Grant Lambda permission to read the SSM parameter at runtime
        region = Stack.of(self).region
        account = Stack.of(self).account
        slack_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{region}:{account}:parameter{SLACK_WEBHOOK_SSM_PATH}"
                ],
            )
        )

        self.topic.add_subscription(
            sns_subscriptions.LambdaSubscription(slack_fn)
        )
