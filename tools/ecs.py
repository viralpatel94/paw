"""ECS deployment tools."""
import boto3

from agent import config


def _ecs():
    return boto3.client("ecs", region_name=config.AWS_REGION)


def register_task_definition(family: str, image_ref: str) -> dict:
    ecs = _ecs()
    latest = ecs.describe_task_definition(taskDefinition=family)["taskDefinition"]

    container_defs = latest["containerDefinitions"]
    container_defs[0]["image"] = image_ref

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
    ecs_client = _ecs()
    cluster = config.CLUSTERS[env]
    ecs_client.update_service(
        cluster=cluster, service=service, taskDefinition=task_def_arn,
        forceNewDeployment=True,
    )
    waiter = ecs_client.get_waiter("services_stable")
    try:
        waiter.wait(cluster=cluster, services=[service],
                    WaiterConfig={"Delay": 15, "MaxAttempts": 40})
    except Exception as e:
        return {"status": "unstable", "env": env, "service": service, "error": str(e)}
    return {"status": "deployed", "env": env, "service": service,
            "task_def_arn": task_def_arn}


def rollback(service: str, env: str) -> dict:
    ecs_client = _ecs()
    cluster = config.CLUSTERS[env]
    current = ecs_client.describe_services(
        cluster=cluster, services=[service]
    )["services"][0]["taskDefinition"]

    family = current.split("/")[-1].split(":")[0]
    revs = ecs_client.list_task_definitions(
        familyPrefix=family, status="ACTIVE", sort="DESC", maxResults=5
    )["taskDefinitionArns"]

    previous = next((r for r in revs if r != current), None)
    if not previous:
        return {"status": "no_previous_revision", "service": service}

    ecs_client.update_service(cluster=cluster, service=service,
                              taskDefinition=previous, forceNewDeployment=True)
    ecs_client.get_waiter("services_stable").wait(
        cluster=cluster, services=[service],
        WaiterConfig={"Delay": 15, "MaxAttempts": 40})
    return {"status": "rolled_back", "service": service, "to": previous}
