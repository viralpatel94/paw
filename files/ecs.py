"""ECS deployment tools.

Includes assuming a dedicated, more-privileged role for PROD updates (defense
in depth): even if the validator were bypassed, the orchestrator's base role
cannot update prod services — only the assumed prod-deploy role can, and the
code only assumes it inside update_service(env='prod').
"""
import os

import boto3

from agent import config
from agent import approvals

_sts = boto3.client("sts", region_name=config.AWS_REGION)

PROD_DEPLOY_ROLE_ARN = os.environ.get("PROD_DEPLOY_ROLE_ARN", "")


def _ecs(env: str):
    """Return an ECS client. For prod, assume the dedicated prod-deploy role."""
    if env == "prod" and PROD_DEPLOY_ROLE_ARN:
        creds = _sts.assume_role(
            RoleArn=PROD_DEPLOY_ROLE_ARN,
            RoleSessionName="cd-agent-prod-deploy",
            DurationSeconds=900,
        )["Credentials"]
        return boto3.client(
            "ecs",
            region_name=config.AWS_REGION,
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
    return boto3.client("ecs", region_name=config.AWS_REGION)


def register_task_definition(family: str, image_ref: str) -> dict:
    """Register a new revision by cloning the latest and swapping the image."""
    ecs = boto3.client("ecs", region_name=config.AWS_REGION)
    latest = ecs.describe_task_definition(taskDefinition=family)["taskDefinition"]

    container_defs = latest["containerDefinitions"]
    # Swap the image on the primary container (first one) — adjust if multi-container.
    container_defs[0]["image"] = image_ref

    # Carry over only the fields register_task_definition accepts.
    kwargs = {
        "family": family,
        "containerDefinitions": container_defs,
        "requiresCompatibilities": latest.get("requiresCompatibilities", []),
        "networkMode": latest.get("networkMode", "awsvpc"),
        "cpu": latest.get("cpu"),
        "memory": latest.get("memory"),
        "executionRoleArn": latest.get("executionRoleArn"),
        "taskRoleArn": latest.get("taskRoleArn"),
        "volumes": latest.get("volumes", []),
    }
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    resp = ecs.register_task_definition(**kwargs)
    arn = resp["taskDefinition"]["taskDefinitionArn"]
    return {"task_def_arn": arn, "family": family, "image_ref": image_ref}


def update_service(env: str, service: str, task_def_arn: str) -> dict:
    """Update an ECS service and wait for it to stabilize."""
    ecs = _ecs(env)
    cluster = config.CLUSTERS[env]
    ecs.update_service(
        cluster=cluster, service=service, taskDefinition=task_def_arn,
        forceNewDeployment=True,
    )
    waiter = ecs.get_waiter("services_stable")
    try:
        waiter.wait(cluster=cluster, services=[service],
                    WaiterConfig={"Delay": 15, "MaxAttempts": 40})
    except Exception as e:
        return {"status": "unstable", "env": env, "service": service,
                "error": str(e)}
    return {"status": "deployed", "env": env, "service": service,
            "task_def_arn": task_def_arn}


def rollback(service: str, env: str) -> dict:
    """Roll back to the previous ACTIVE task definition revision."""
    ecs = _ecs(env)
    cluster = config.CLUSTERS[env]
    current = ecs.describe_services(
        cluster=cluster, services=[service]
    )["services"][0]["taskDefinition"]

    family = current.split("/")[-1].split(":")[0]
    revs = ecs.list_task_definitions(
        familyPrefix=family, status="ACTIVE", sort="DESC", maxResults=5
    )["taskDefinitionArns"]

    previous = next((r for r in revs if r != current), None)
    if not previous:
        return {"status": "no_previous_revision", "service": service}

    ecs.update_service(cluster=cluster, service=service,
                       taskDefinition=previous, forceNewDeployment=True)
    ecs.get_waiter("services_stable").wait(
        cluster=cluster, services=[service],
        WaiterConfig={"Delay": 15, "MaxAttempts": 40})
    return {"status": "rolled_back", "service": service, "to": previous}


def request_prod_approval(service: str, task_def_arn: str, summary: str) -> dict:
    """Create a pending approval and notify humans. Does NOT deploy."""
    sha = os.environ.get("COMMIT_SHA", "")
    request_id = approvals.create_pending(
        service=service, task_def_arn=task_def_arn,
        commit_sha=sha, summary=summary,
    )
    _notify_approvers(request_id, service, task_def_arn, summary)
    return {"request_id": request_id, "status": "pending",
            "note": "Awaiting human approval. Poll check_approval_status."}


def check_approval_status(request_id: str) -> dict:
    rec = approvals.get_approval(request_id)
    if rec is None:
        return {"status": "not_found", "request_id": request_id}
    return {"request_id": request_id, "status": rec["status"],
            "task_def_arn": rec.get("task_def_arn")}


def _notify_approvers(request_id, service, task_def_arn, summary):
    """Post to Slack (or GitHub deployment). Best-effort; see webhook/slack.py."""
    try:
        from webhook import slack
        slack.post_approval_request(request_id, service, task_def_arn, summary)
    except Exception:
        pass  # don't fail the run if notification fails; status is still pending
