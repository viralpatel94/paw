# Paw

A browser-based AI sketch studio that converts dog photos to pencil sketches — served as a static site via nginx on AWS ECS, with a Claude-powered CD agent that deploys every push to `main`.

## How it works

```
git push main
    └── GitHub Actions
            ├── build Docker image → push to ECR
            ├── register ECS task definition (pinned by digest)
            ├── deploy to dev (paw-dev cluster)
            └── run smoke test → report success
```

---

## How the agent works

### The core loop: reason → act → observe

The agent is built on Claude's tool use API. Every turn, Claude receives the conversation history and a list of tools it can call. It responds with either a tool call or a final text answer. The loop (`agent/loop.py`) drives this cycle:

```
while not done:
    response = claude(messages, tools)
    if response is tool_use:
        result = validate_and_execute(tool, args)
        messages.append(result)   # feed result back so Claude can observe
    else:
        break                     # Claude is done reasoning
```

Claude never directly touches AWS. It proposes tool calls; the validator executes them.

### The tools

Claude has six tools it can call:

| Tool | What it does |
|---|---|
| `get_commit_diff` | Read what changed in the commit |
| `build_and_push_image` | Build Docker image, push to ECR, return immutable digest |
| `register_task_definition` | Create a new ECS task def revision pinned to the digest |
| `deploy_to_dev` | Update the dev ECS service, wait for stability |
| `run_smoke_test` | HTTP health check against the dev service |
| `rollback` | Revert the dev service to its previous task definition |

### The validator: propose vs. dispose

The most important security boundary is `agent/validator.py`. Claude *proposes* tool calls; the validator *disposes* (actually executes) them. Every call is checked before anything touches AWS:

- **Allowlists**: the agent can only act on repos, services, and task families explicitly listed in `agent/config.py`. If Claude hallucinates a service name, the call is rejected and Claude sees a `PolicyError`.
- **Immutable image refs**: the validator rejects any `register_task_definition` call where the image isn't pinned by digest (`@sha256:...`). Floating tags like `:latest` are blocked.

### Prompt injection defense

Commit diffs and messages are untrusted input. The agent wraps all commit data in `<commit_data>` tags and the system prompt instructs Claude to treat anything inside as data, not instructions. If a commit message says "skip tests" or "ignore previous rules", Claude is instructed to ignore it and the validator enforces the allowlists regardless.

---

## Repo structure

```
index.html          # the app — runs entirely in the browser
Dockerfile          # nginx:alpine serving index.html
nginx.conf          # custom nginx config
agent/
  loop.py           # reason → act → observe loop
  validator.py      # policy enforcement — the security boundary
  tool_defs.py      # tool schemas exposed to Claude
  config.py         # allowlists (repos, services, clusters)
tools/
  build.py          # Docker build + ECR push
  ecs.py            # task definition registration, service updates, rollback
  git.py            # commit diff parsing
  smoke.py          # HTTP health checks
webhook/
  server.py         # HTTP approval endpoint (for future prod use)
.github/workflows/
  deploy.yml        # CI/CD pipeline (build → dev deploy → smoke test)
  scale.yml         # scheduled scale up/down to save cost
iam_policies.json   # least-privilege IAM role definitions
test_flow.py        # offline control-flow tests (no AWS/API needed)
requirements.txt
```

## AWS resources

| Resource | Name |
|---|---|
| ECR repo | `paw-web` |
| ECS cluster | `paw-dev` |
| ECS service | `paw-web-dev` |
| Task family | `paw-web` |
| IAM roles | `cd-agent-base`, `cd-agent-build`, `cd-agent-deploy-dev` |

## Accessing the app

The dev service runs on Fargate with a public IP assigned per task. Since there's no load balancer, the IP changes on every deploy.

Get the current IP:

```bash
TASK=$(aws ecs list-tasks --cluster paw-dev --desired-status RUNNING --query 'taskArns[0]' --output text)
ENI=$(aws ecs describe-tasks --cluster paw-dev --tasks $TASK \
  --query 'tasks[0].attachments[0].details[?name==`networkInterfaceId`].value' --output text)
aws ec2 describe-network-interfaces --network-interface-ids $ENI \
  --query 'NetworkInterfaces[0].Association.PublicIp' --output text
```

Then open `http://<ip>` in your browser. Port 80 is restricted to your IP in the default security group — update the inbound rule if your IP changes:

```bash
aws ec2 authorize-security-group-ingress \
  --group-id sg-0901bb74 \
  --protocol tcp --port 80 --cidr <your-ip>/32
```

## Setup

### GitHub secrets

| Secret | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | IAM user access key |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret key |
| `ANTHROPIC_API_KEY` | Anthropic API key |

### IAM roles

Create the three roles from `iam_policies.json`. Replace `ACCOUNT` and `REGION` with your values.

## Running tests

```bash
python3 test_flow.py
```

Tests verify the agent control flow offline — no AWS or Anthropic API needed.
