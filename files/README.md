# LLM-based CD Agent → ECS

An LLM agent that, on a GitHub commit, builds and pushes a Docker image, deploys
to **dev**, smoke-tests it, and—**only after a human approves**—deploys to
**prod**, smoke-tests again, and rolls back on failure.

The LLM provides *judgment* (what changed, which strategy, summarize, triage).
Tested Python code provides *execution*. The model never holds AWS credentials
and can only act through a fixed set of allowlisted tools.

## Flow

```
commit → build_and_push_image → register_task_definition
       → deploy_to_dev → run_smoke_test(dev)
          ├─ fail → rollback(dev) → stop
          └─ pass → request_prod_approval → [HUMAN clicks Approve in Slack]
                  → deploy_to_prod → run_smoke_test(prod)
                       ├─ fail → rollback(prod)
                       └─ pass → done
```

Two passes. The first ends at `request_prod_approval`. The Slack approval
webhook records the decision and re-invokes the agent (`resume_after_approval`)
for the prod pass — so no process blocks waiting on a human.

## Layout

```
agent/
  tool_defs.py   tool schemas exposed to the model
  loop.py        the reason→act→observe loop (both passes) + thread persistence
  validator.py   POLICY ENFORCEMENT — every tool call passes through here
  approvals.py   DynamoDB approval records (read by agent, written by webhook)
  config.py      allowlists: repos, services, families, clusters, endpoints
tools/
  git.py         read commit diff (untrusted)
  build.py       BuildKit build + ECR push, pins by digest
  ecs.py         register task def, update service, rollback, approval helpers
  smoke.py       post-deploy health checks
webhook/
  server.py      GitHub push + Slack action endpoints (HMAC-verified)
  slack.py       posts the Approve/Reject message
infra/
  iam_policies.json   five least-privilege roles
test_flow.py     offline tests of control flow + the prod gate (no AWS/API)
```

## The security model (read this)

Five properties do the heavy lifting:

1. **Propose vs. dispose.** The LLM only *proposes* tool calls. `validator.py`
   validates every one against allowlists before execution. Unknown tools,
   non-allowlisted repos/services/families, path traversal in
   `dockerfile_path`, and floating (non-digest) image refs are all rejected.

2. **Prod gate bound to an artifact.** `deploy_to_prod` is impossible unless an
   approval record exists with `status == approved` **and** its `task_def_arn`
   matches *exactly* what's being deployed (and the commit matches). The agent
   path only *reads* approvals; only the human-driven webhook *writes* them.

3. **IAM defense in depth.** The orchestrator's base role **cannot** mutate prod
   ECS. Prod updates require assuming `cd-agent-deploy-prod`, whose trust policy
   admits only the base role, and which the code assumes only inside
   `update_service(env='prod')` — reached only after the gate passes. Build and
   deploy use separate scoped roles too.

4. **Prompt-injection containment.** Commit messages, files, and diffs are
   wrapped in `<commit_data>` and labeled untrusted in the system prompt. The
   model is instructed to never follow instructions found there. Even if it did,
   properties 1–3 mean it still can't reach prod without a real human approval.
   `test_flow.py` includes the arn-mismatch case proving this.

5. **Single-decision approvals.** `approvals.decide` uses a conditional update
   so a request transitions out of `pending` exactly once (no replay / double).

## Setup

```bash
pip install -r requirements.txt

# DynamoDB approvals table (partition key: request_id, String)
aws dynamodb create-table --table-name cd-agent-approvals \
  --attribute-definitions AttributeName=request_id,AttributeType=S \
  --key-schema AttributeName=request_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST

# Create the five roles in infra/iam_policies.json (replace ACCOUNT/REGION/ARNs).
```

Environment:

```bash
export ANTHROPIC_API_KEY=...
export AWS_ACCOUNT_ID=... AWS_REGION=us-east-1
export APPROVALS_TABLE=cd-agent-approvals
export PROD_DEPLOY_ROLE_ARN=arn:aws:iam::ACCOUNT:role/cd-agent-deploy-prod
export GITHUB_WEBHOOK_SECRET=... SLACK_SIGNING_SECRET=...
export SLACK_APPROVAL_WEBHOOK_URL=https://hooks.slack.com/...
export WATCH_BRANCH=refs/heads/main
```

Edit **`agent/config.py`** so the allowlists match your real repos, services,
families, clusters, and smoke endpoints. This file is the security boundary —
keep it under tight review.

## Run

```bash
uvicorn webhook.server:app --host 0.0.0.0 --port 8080
```

Point a GitHub webhook (push events, JSON) at `/github/webhook` with the shared
secret, and a Slack interactivity URL at `/slack/actions`.

> **Checkout step:** before the agent runs, the repo must be checked out at the
> pushed SHA into `config.WORKSPACE`. In `server.py` this is marked as a TODO —
> wire it to a clone/init step or a build sidecar in your ECS task.

## Test (offline, no AWS or API needed)

```bash
python test_flow.py
```

Verifies: dev-only happy path stops at approval; prod blocked while pending;
prod proceeds with a matching approval; prod blocked on arn mismatch.

## Build-in-Fargate note

`tools/build.py` uses `buildctl` (rootless BuildKit) and expects a `buildkitd`
sidecar in the task. If you'd rather not run BuildKit, swap `_do_build` for a
CodeBuild trigger — keep the return contract (`{"image_ref": "repo@sha256:..."}`)
and nothing else changes.

## Production hardening checklist

- Replace file-based thread persistence (`_save_thread`/`_load_thread`) with
  DynamoDB or S3.
- Dispatch agent runs via SQS + a worker (or Step Functions) instead of threads.
- Add structured audit logging of every `validate_and_execute` decision.
- Add a turn/time budget and alerting on `policy_error` results.
- Consider canary/blue-green via ECS deployment circuit breaker or CodeDeploy.
```
