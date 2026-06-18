"""Policy enforcement: validates all tool calls before execution."""
from agent import config
from tools import build, ecs, git, smoke

_IMPL = {
    "get_commit_diff": git.get_commit_diff,
    "build_and_push_image": build.build_and_push_image,
    "register_task_definition": ecs.register_task_definition,
    "deploy_to_dev": lambda **kw: ecs.update_service(env="dev", **kw),
    "run_smoke_test": smoke.run_smoke_test,
    "rollback": ecs.rollback,
}


class PolicyError(Exception):
    pass


def _reject(msg: str):
    raise PolicyError(msg)


def validate_and_execute(name: str, args: dict, ctx: dict) -> dict:
    if name not in _IMPL:
        _reject(f"Unknown tool: {name}")

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
        ref = args.get("image_ref", "")
        if "@sha256:" not in ref:
            _reject("image_ref must be an immutable digest (repo@sha256:...).")

    elif name in ("deploy_to_dev", "run_smoke_test", "rollback"):
        svc = args.get("service")
        if svc not in config.ALLOWED_SERVICES["dev"]:
            _reject(f"Service not allowlisted for dev: {svc!r}")

    return _IMPL[name](**args)
