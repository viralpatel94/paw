"""Policy enforcement: the 'disposer'.

The LLM *proposes* tool calls; this module *disposes*. Every tool call is
routed through validate_and_execute before anything touches AWS. This is the
single most security-critical file in the project.

Two non-negotiable properties:
  1. The agent can only act on allowlisted repos/services/families.
  2. deploy_to_prod is impossible without a human approval record that is
     bound to the EXACT task_def_arn being deployed. Nothing the LLM emits
     (and nothing injected via commit content) can manufacture that record.
"""
import os

from agent import config
from agent.approvals import get_approval
from tools import build, ecs, git, smoke

# Maps tool name -> implementation callable.
_IMPL = {
    "get_commit_diff": git.get_commit_diff,
    "build_and_push_image": build.build_and_push_image,
    "register_task_definition": ecs.register_task_definition,
    "deploy_to_dev": lambda **kw: ecs.update_service(env="dev", **kw),
    "request_prod_approval": ecs.request_prod_approval,
    "check_approval_status": ecs.check_approval_status,
    "deploy_to_prod": None,  # handled inline below (special-cased)
    "run_smoke_test": smoke.run_smoke_test,
    "rollback": ecs.rollback,
}


class PolicyError(Exception):
    """Raised when a proposed tool call violates policy. Surfaced to the agent."""


def _reject(msg: str):
    raise PolicyError(msg)


def validate_and_execute(name: str, args: dict, ctx: dict) -> dict:
    """Validate a single tool call against policy, then execute it.

    Args:
        name: tool name the LLM chose.
        args: tool input from the LLM (schema already enforced by the API).
        ctx:  run context (commit sha, run_id, etc.) the LLM cannot set.

    Returns: a JSON-serializable dict (the tool_result content).
    Raises:  PolicyError on any violation. The loop converts this into a
             tool_result error so the agent can react (e.g. stop, rollback).
    """
    if name not in _IMPL:
        _reject(f"Unknown tool: {name}")

    # ---- per-tool policy checks -------------------------------------------
    if name == "build_and_push_image":
        repo = args.get("ecr_repo")
        if repo not in config.ALLOWED_ECR_REPOS:
            _reject(f"ECR repo not allowlisted: {repo!r}")
        dfp = args.get("dockerfile_path", "")
        if dfp.startswith("/") or ".." in dfp.split("/"):
            _reject("dockerfile_path must be a relative path inside the repo.")

    elif name == "register_task_definition":
        fam = args.get("family")
        if fam not in config.ALLOWED_FAMILIES:
            _reject(f"Task definition family not allowlisted: {fam!r}")
        # Image must be the immutable digest form we produced, not a floating tag.
        ref = args.get("image_ref", "")
        if "@sha256:" not in ref:
            _reject("image_ref must be an immutable digest (repo@sha256:...).")

    elif name == "deploy_to_dev":
        svc = args.get("service")
        if svc not in config.ALLOWED_SERVICES["dev"]:
            _reject(f"Service not allowlisted for dev: {svc!r}")

    elif name == "request_prod_approval":
        svc = args.get("service")
        if svc not in config.ALLOWED_SERVICES["prod"]:
            _reject(f"Service not allowlisted for prod: {svc!r}")

    elif name == "deploy_to_prod":
        return _execute_prod_deploy(args, ctx)

    elif name in ("run_smoke_test", "rollback"):
        svc, env = args.get("service"), args.get("env")
        if env not in ("dev", "prod"):
            _reject(f"Invalid env: {env!r}")
        if svc not in config.ALLOWED_SERVICES[env]:
            _reject(f"Service not allowlisted for {env}: {svc!r}")

    # ---- execute ----------------------------------------------------------
    impl = _IMPL[name]
    return impl(**args)


def _execute_prod_deploy(args: dict, ctx: dict) -> dict:
    """Prod deploy gate. The ONLY path to mutate a prod service.

    Enforces, in order:
      - service is an allowlisted prod service
      - an approval record exists for request_id
      - that record is status == 'approved'
      - the approved task_def_arn matches EXACTLY what we're deploying
      - (defense in depth) the approval is for this run's commit
    """
    svc = args.get("service")
    task_def_arn = args.get("task_def_arn")
    request_id = args.get("request_id")

    if svc not in config.ALLOWED_SERVICES["prod"]:
        _reject(f"Service not allowlisted for prod: {svc!r}")

    record = get_approval(request_id)
    if record is None:
        _reject(f"No approval record for request_id={request_id!r}.")
    if record.get("status") != "approved":
        _reject(
            f"Prod deploy blocked: approval status is "
            f"{record.get('status')!r}, not 'approved'."
        )
    if record.get("task_def_arn") != task_def_arn:
        _reject(
            "Prod deploy blocked: approved task_def_arn does not match the "
            "task definition being deployed. Approval is bound to a specific "
            "artifact and cannot be reused."
        )
    if record.get("commit_sha") and record["commit_sha"] != ctx.get("sha"):
        _reject("Prod deploy blocked: approval is for a different commit.")

    # All checks passed. Assume the dedicated prod-deploy role and update.
    return ecs.update_service(env="prod", service=svc, task_def_arn=task_def_arn)
