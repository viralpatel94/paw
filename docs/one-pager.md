# Paw CD Agent — One Pager

**A Claude-powered continuous deployment system for AWS ECS**

---

## What it does

Every push to `main` triggers a GitHub Actions job that runs a Claude agent. The agent autonomously reads the diff, builds a Docker image, pushes it to ECR, registers a new ECS task definition, deploys to the dev cluster, and runs a smoke test — rolling back automatically on failure.

---

## Architecture

The agent is built on Claude's tool use API. Claude receives the conversation history and a fixed set of tools, reasons about what to do, and emits tool calls. Each result is fed back so Claude can observe the outcome before deciding the next step. The loop runs until Claude emits a final text answer.

```
while not done:
    response = claude(messages, tools)
    if response is tool_use:
        result = validate_and_execute(tool, args)
        messages.append(result)   # Claude observes and continues
    else:
        break
```

---

## The validator: propose vs. dispose

Claude never touches AWS directly. Every tool call passes through `validator.py` first, which enforces:

- **Allowlists** — only permitted ECR repos, ECS services, and task families can be acted on. If Claude hallucinates a name, the call is blocked and Claude sees a `PolicyError`.
- **Immutable image refs** — floating tags like `:latest` are rejected. Only `@sha256:...` digests are accepted, ensuring every deploy is pinned to an exact artifact.

---

## Prompt injection defense

Commit diffs are untrusted input. All commit data is wrapped in `<commit_data>` tags and the system prompt tells Claude to treat the contents as data, never as instructions. Even if Claude were tricked, the validator enforces the same rules regardless of what Claude says.

---

## Tools available to Claude

| Tool | Purpose |
|---|---|
| `get_commit_diff` | Understand what changed |
| `build_and_push_image` | Build image, push to ECR, return digest |
| `register_task_definition` | Pin new task def to exact image digest |
| `deploy_to_dev` | Update ECS service, wait for stability |
| `run_smoke_test` | HTTP health check |
| `rollback` | Restore previous task definition on failure |

---

## Stack

Claude (`claude-sonnet-4-6`) · Python · AWS ECS Fargate · ECR · GitHub Actions

---

*Flow diagram: `docs/flow-diagram.svg`*
