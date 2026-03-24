"""SNS Topic + Slack dispatcher Lambda subscription."""
from aws_cdk import (
    Duration,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_sns as sns,
    aws_sns_subscriptions as sns_subscriptions,
    aws_ssm as ssm,
)
from constructs import Construct

SLACK_WEBHOOK_SSM_PATH = "/dr-alert/slack-webhook-url"


class NotificationConstruct(Construct):
    """SNS topic for GPRI level changes + Slack dispatcher Lambda."""

    def __init__(self, scope: Construct, id: str) -> None:
        super().__init__(scope, id)

        self.topic = sns.Topic(
            self,
            "AlertTopic",
            topic_name="dr-alert-gpri-changes",
            display_name="DR Geopolitical Alert — GPRI Level Changes",
        )

        slack_webhook_url = ssm.StringParameter.value_for_string_parameter(
            self, SLACK_WEBHOOK_SSM_PATH
        )

        slack_fn = lambda_.Function(
            self,
            "SlackDispatcher",
            function_name="dr-alert-slack-dispatcher",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="notify.slack_dispatcher.handler",
            code=lambda_.Code.from_asset("src"),
            memory_size=256,
            timeout=Duration.seconds(30),
            environment={
                "SLACK_WEBHOOK_URL": slack_webhook_url,
            },
        )

        slack_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:us-west-2:926093770964:parameter{SLACK_WEBHOOK_SSM_PATH}"
                ],
            )
        )

        self.topic.add_subscription(
            sns_subscriptions.LambdaSubscription(slack_fn)
        )
