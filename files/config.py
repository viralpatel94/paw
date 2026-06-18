"""Central configuration and allowlists.

Everything the agent is permitted to touch is enumerated here. If a service
or repo isn't in these lists, the validator rejects the call regardless of
what the LLM decides. Keep this file under tight review/access control.
"""
import os

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "")

# Repos the agent may push to (ECR repo names, not URIs).
ALLOWED_ECR_REPOS = {
    "myapp-api",
    "myapp-worker",
}

# ECS clusters per environment.
CLUSTERS = {
    "dev": os.environ.get("DEV_CLUSTER", "myapp-dev"),
    "prod": os.environ.get("PROD_CLUSTER", "myapp-prod"),
}

# Services the agent may update, per environment.
ALLOWED_SERVICES = {
    "dev": {"myapp-api-dev", "myapp-worker-dev"},
    "prod": {"myapp-api-prod", "myapp-worker-prod"},
}

# Task definition families the agent may register.
ALLOWED_FAMILIES = {
    "myapp-api",
    "myapp-worker",
}

# Smoke-test endpoints per service+env. {} means "no endpoint, skip HTTP check".
SMOKE_ENDPOINTS = {
    ("myapp-api-dev", "dev"): "https://dev.myapp.internal/healthz",
    ("myapp-api-prod", "prod"): "https://myapp.example.com/healthz",
}

# DynamoDB table that stores approval records. Only the approval webhook writes
# the "approved" status; the agent can only read it.
APPROVALS_TABLE = os.environ.get("APPROVALS_TABLE", "cd-agent-approvals")

# Where build happens. The repo is checked out here at the target commit.
WORKSPACE = os.environ.get("WORKSPACE", "/workspace/repo")

# Model used for the agent loop.
AGENT_MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-6")
