#!/usr/bin/env python3
"""CDK entry point for DR Geopolitical Alert system."""
import os

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks, NagSuppressions
from stacks.alert_stack import DrGeopoliticalAlertStack

app = cdk.App()

stack = DrGeopoliticalAlertStack(
    app,
    "DrGeopoliticalAlertStack",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEPLOY_REGION", "us-west-2"),
    ),
    description="AWS DR Geopolitical Alert - GPRI early warning system",
)

# Enable AWS Solutions security checks
cdk.Aspects.of(app).add(AwsSolutionsChecks())

# Suppress legitimate findings at the stack level
NagSuppressions.add_stack_suppressions(
    stack,
    [
        {
            "id": "AwsSolutions-DDB3",
            "reason": (
                "DynamoDB tables use TTL-based expiry (7-day signals, 90-day GPRI). "
                "PITR is intentionally disabled to reduce cost for this monitoring workload."
            ),
        },
        {
            "id": "AwsSolutions-IAM4",
            "reason": (
                "Lambda functions use AWSLambdaBasicExecutionRole managed policy, "
                "which is the standard minimum for Lambda CloudWatch Logs access."
            ),
        },
        {
            "id": "AwsSolutions-IAM5",
            "reason": (
                "cloudwatch:PutMetricData requires resource '*' (AWS does not support "
                "resource-level restrictions for this action). "
                "SSM read is scoped to /dr-alert/* prefix."
            ),
        },
        {
            "id": "AwsSolutions-SNS2",
            "reason": (
                "SNS topic carries operational GPRI level-change notifications only. "
                "No PII or sensitive data — KMS encryption not required."
            ),
        },
        {
            "id": "AwsSolutions-SNS3",
            "reason": (
                "SNS topic is used for internal Lambda-to-Slack notifications only. "
                "All publishers are within the same AWS account; SSL enforcement "
                "is unnecessary for this internal operational channel."
            ),
        },
        {
            "id": "AwsSolutions-SQS3",
            "reason": (
                "This SQS queue IS the Dead Letter Queue for all Lambdas. "
                "Adding a DLQ to a DLQ would create unnecessary recursion."
            ),
        },
        {
            "id": "AwsSolutions-SQS4",
            "reason": (
                "DLQ is an internal Lambda failure sink, not a consumer-facing queue. "
                "SSL enforcement policy adds no security value here."
            ),
        },
        {
            "id": "AwsSolutions-L1",
            "reason": (
                "Lambda functions use Python 3.13 (ARM64), which is the latest available "
                "runtime at time of deployment."
            ),
        },
    ],
)

app.synth()
