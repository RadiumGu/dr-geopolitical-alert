#!/usr/bin/env python3
"""CDK entry point for DR Geopolitical Alert system."""
import os

import aws_cdk as cdk
from stacks.alert_stack import DrGeopoliticalAlertStack

app = cdk.App()

DrGeopoliticalAlertStack(
    app,
    "DrGeopoliticalAlertStack",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEPLOY_REGION", "us-west-2"),
    ),
    description="AWS DR Geopolitical Alert - GPRI early warning system",
)

app.synth()
