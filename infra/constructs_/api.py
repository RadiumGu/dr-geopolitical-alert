"""Public GPRI query API via Lambda Function URL."""
from aws_cdk import (
    Duration,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    CfnOutput,
)
from constructs import Construct

GPRI_TABLE_NAME = "dr-alert-gpri"


class ApiConstruct(Construct):
    """Lambda + Function URL for public GPRI queries."""

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

        # Function URL — no auth (public read-only)
        fn_url = self.fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=["*"],
                allowed_methods=[lambda_.HttpMethod.GET],
            ),
        )

        self.url = fn_url.url

        CfnOutput(self, "GpriQueryUrl", value=fn_url.url, description="Public GPRI Query API URL")
