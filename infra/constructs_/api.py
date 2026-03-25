"""GPRI query API via Lambda.

The Lambda function is always created (for internal use / SDK invocation).
A public Function URL is **disabled by default** for security.

To enable public HTTP access, set context variable:
    cdk deploy -c enable_api_url=true

Or in cdk.json:
    { "context": { "enable_api_url": true } }
"""
from aws_cdk import (
    Duration,
    CfnOutput,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
)
from constructs import Construct

GPRI_TABLE_NAME = "dr-alert-gpri"


class ApiConstruct(Construct):
    """Lambda for GPRI queries, with optional public Function URL."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        gpri_table: dynamodb.Table,
    ) -> None:
        super().__init__(scope, id)

        self.fn = lambda_.Function(
            self,
            "GpriQuery",
            function_name="dr-alert-gpri-query",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="api.gpri_query.handler",
            code=lambda_.Code.from_asset("src"),
            memory_size=128,
            timeout=Duration.seconds(10),
            environment={
                "GPRI_TABLE": GPRI_TABLE_NAME,
            },
        )

        gpri_table.grant_read_data(self.fn)

        # Optional: expose a public Function URL (disabled by default)
        enable_url = self.node.try_get_context("enable_api_url")
        if enable_url in (True, "true", "True"):
            fn_url = self.fn.add_function_url(
                auth_type=lambda_.FunctionUrlAuthType.NONE,
            )
            CfnOutput(
                self,
                "GpriQueryUrl",
                value=fn_url.url,
                description="Public GPRI Query API endpoint",
            )
