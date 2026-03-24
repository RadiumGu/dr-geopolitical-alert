"""DynamoDB tables for signals and GPRI history."""
from aws_cdk import (
    RemovalPolicy,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


class TablesConstruct(Construct):
    def __init__(self, scope: Construct, id: str) -> None:
        super().__init__(scope, id)

        # ── signals table (raw signal data, TTL 7 days) ──
        self.signals_table = dynamodb.Table(
            self,
            "SignalsTable",
            table_name="dr-alert-signals",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl",
            point_in_time_recovery=False,
        )

        # GSI: query latest signals by class across all regions
        self.signals_table.add_global_secondary_index(
            index_name="signal_class-collected_at-index",
            partition_key=dynamodb.Attribute(
                name="signal_class", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="collected_at", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # ── gpri table (GPRI score history, TTL 90 days) ──
        self.gpri_table = dynamodb.Table(
            self,
            "GpriTable",
            table_name="dr-alert-gpri",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl",
            point_in_time_recovery=False,
        )

        # GSI: query regions by alert level
        self.gpri_table.add_global_secondary_index(
            index_name="level-gpri-index",
            partition_key=dynamodb.Attribute(
                name="level", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="gpri", type=dynamodb.AttributeType.NUMBER
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )
