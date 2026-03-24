"""Placeholder constructs — to be implemented in subsequent days."""
from aws_cdk import aws_dynamodb as dynamodb, aws_sns as sns
from constructs import Construct


class CollectorsConstruct(Construct):
    """Week 1 Day 2+: Signal collector Lambdas."""
    def __init__(self, scope: Construct, id: str, *, signals_table: dynamodb.Table) -> None:
        super().__init__(scope, id)
        self.signals_table = signals_table
        # TODO: 7 Lambda functions + EventBridge Schedulers


class GpriEngineConstruct(Construct):
    """Week 1 Day 3: GPRI calculation engine."""
    def __init__(
        self, scope: Construct, id: str, *,
        signals_table: dynamodb.Table,
        gpri_table: dynamodb.Table,
        sns_topic: sns.Topic,
    ) -> None:
        super().__init__(scope, id)
        # TODO: 1 Lambda + EventBridge Scheduler


class NotificationConstruct(Construct):
    """Week 1 Day 4: SNS topic + Slack dispatcher."""
    def __init__(self, scope: Construct, id: str) -> None:
        super().__init__(scope, id)
        self.topic = sns.Topic(
            self, "AlertTopic",
            topic_name="dr-alert-gpri-changes",
            display_name="DR Geopolitical Alert — GPRI Level Changes",
        )
        # TODO: Slack webhook Lambda subscription


class DashboardConstruct(Construct):
    """Week 1 Day 5: CloudWatch Dashboard."""
    def __init__(self, scope: Construct, id: str) -> None:
        super().__init__(scope, id)
        # TODO: CloudWatch Dashboard
