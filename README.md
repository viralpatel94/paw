# Paw

A browser-based AI sketch studio that converts dog photos to pencil sketches — served as a static site via nginx on AWS ECS, with a Claude-powered CD agent that deploys every push to `main`.

## How it works

```
git push main
    └── GitHub Actions
            ├── build Docker image → push to ECR
            ├── register ECS task definition (pinned by digest)
            ├── deploy to dev (paw-dev cluster)
            └── pause for human approval
                    └── [Approve in GitHub UI]
                            └── deploy to prod (paw-prod cluster)
```

The CD agent is powered by Claude (`claude-sonnet-4-6`). It reasons about what changed, builds and deploys the image, and gates production on a human approval click — enforced both in the agent's system prompt and at the IAM layer.

## Repo structure

```
index.html          # the app — runs entirely in the browser
Dockerfile          # nginx:alpine serving index.html
nginx.conf          # custom nginx config
agent/              # Claude agent loop + policy enforcement
tools/              # AWS tool implementations (ECR, ECS, git, smoke)
webhook/            # HTTP server for GitHub push + approval endpoints
.github/workflows/  # CI/CD pipeline
iam_policies.json   # least-privilege IAM role definitions
test_flow.py        # offline control-flow tests (no AWS/API needed)
requirements.txt    # Python dependencies
```

## AWS resources

| Resource | Name |
|---|---|
| ECR repo | `paw-web` |
| ECS clusters | `paw-dev`, `paw-prod` |
| ECS services | `paw-web-dev`, `paw-web-prod` |
| Task family | `paw-web` |
| DynamoDB table | `cd-agent-approvals` |
| IAM roles | `cd-agent-base`, `cd-agent-build`, `cd-agent-deploy-dev`, `cd-agent-deploy-prod`, `cd-agent-webhook` |

## Setup

### GitHub secrets

| Secret | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | IAM user access key |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `APPROVAL_TOKEN` | Bearer token for the approval endpoint |

### GitHub environment

Create a `production` environment at **Settings → Environments** and add yourself as a required reviewer. The `deploy-prod` job will pause until you approve.

### IAM roles

Create the five roles from `iam_policies.json`. Replace `ACCOUNT` and `REGION` with your values.

## Running tests

```bash
arch -arm64 python3 test_flow.py
```

No AWS or Anthropic API needed — all external calls are stubbed.
