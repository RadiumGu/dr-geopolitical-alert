"""Main CDK Stack — assembles all constructs."""
from aws_cdk import Stack
from constructs import Construct
from constructs_.tables import TablesConstruct
from constructs_.collectors import CollectorsConstruct
from constructs_.gpri_engine import GpriEngineConstruct
from constructs_.notification import NotificationConstruct
from constructs_.dashboard import DashboardConstruct


class DrGeopoliticalAlertStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # 1. DynamoDB tables
        tables = TablesConstruct(self, "Tables")

        # 2. Notification (SNS + Slack) — created before engine so topic is available
        notification = NotificationConstruct(self, "Notification")

        # 3. GPRI Engine
        engine = GpriEngineConstruct(
            self,
            "Engine",
            signals_table=tables.signals_table,
            gpri_table=tables.gpri_table,
            sns_topic=notification.topic,
        )

        # 4. Signal Collectors
        collectors = CollectorsConstruct(
            self,
            "Collectors",
            signals_table=tables.signals_table,
        )

        # 5. CloudWatch Dashboard
        dashboard = DashboardConstruct(self, "Dashboard")
