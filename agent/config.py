"""Central configuration and allowlists for the paw CD agent."""
import os

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "")

ALLOWED_ECR_REPOS = {"paw-web"}

CLUSTERS = {
    "dev": os.environ.get("DEV_CLUSTER", "paw-dev"),
    "prod": os.environ.get("PROD_CLUSTER", "paw-prod"),
}

ALLOWED_SERVICES = {
    "dev": {"paw-web-dev"},
    "prod": {"paw-web-prod"},
}

ALLOWED_FAMILIES = {"paw-web"}

# Replace placeholder DNS values after ALBs are provisioned.
SMOKE_ENDPOINTS = {
    ("paw-web-dev", "dev"): os.environ.get("DEV_SMOKE_URL", ""),
    ("paw-web-prod", "prod"): os.environ.get("PROD_SMOKE_URL", ""),
}

APPROVALS_TABLE = os.environ.get("APPROVALS_TABLE", "cd-agent-approvals")

WORKSPACE = os.environ.get("WORKSPACE", "/workspace/repo")

AGENT_MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-6")
