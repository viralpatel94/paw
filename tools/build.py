"""Build & push tool.

Builds the image with BuildKit and pushes to ECR, then resolves the immutable
digest so downstream steps pin an exact artifact (never a floating tag).

Running Docker builds inside Fargate is awkward (no Docker daemon). Two common
options:
  (a) Use the AWS-managed build: hand off to CodeBuild (recommended in Fargate).
  (b) Run buildkitd as a sidecar / use `buildctl` against a remote builder.

This implementation shells out to `buildctl` (rootless BuildKit), which works
in a Fargate task with a buildkitd sidecar. Swap _do_build for a CodeBuild
trigger if you prefer the managed path — the return contract is the same.
"""
import base64
import json
import subprocess

import boto3

from agent import config


def _ecr_client():
    return boto3.client("ecr", region_name=config.AWS_REGION)


def _registry() -> str:
    return f"{config.AWS_ACCOUNT_ID}.dkr.ecr.{config.AWS_REGION}.amazonaws.com"


def _ecr_login() -> tuple[str, str]:
    tok = _ecr_client().get_authorization_token()["authorizationData"][0]
    user, pwd = base64.b64decode(tok["authorizationToken"]).decode().split(":", 1)
    return user, pwd


def _do_build(dockerfile_path: str, image_uri: str, build_args: dict | None):
    """Build with buildkit and push. Raises CalledProcessError on failure."""
    user, pwd = _ecr_login()
    # buildctl pushes directly to the registry.
    cmd = [
        "buildctl", "build",
        "--frontend", "dockerfile.v0",
        "--local", f"context={config.WORKSPACE}",
        "--local", f"dockerfile={config.WORKSPACE}",
        "--opt", f"filename={dockerfile_path}",
        "--output", f"type=image,name={image_uri},push=true",
    ]
    for k, v in (build_args or {}).items():
        cmd += ["--opt", f"build-arg:{k}={v}"]
    # Registry auth passed via env that buildkit reads (~/.docker/config.json).
    _write_docker_auth(user, pwd)
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _write_docker_auth(user: str, pwd: str):
    import os
    auth = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    cfg = {"auths": {_registry(): {"auth": auth}}}
    os.makedirs(os.path.expanduser("~/.docker"), exist_ok=True)
    with open(os.path.expanduser("~/.docker/config.json"), "w") as f:
        json.dump(cfg, f)


def _resolve_digest(repo: str, tag: str) -> str:
    """Return repo@sha256:... for the pushed tag."""
    resp = _ecr_client().describe_images(
        repositoryName=repo, imageIds=[{"imageTag": tag}]
    )
    digest = resp["imageDetails"][0]["imageDigest"]
    return f"{_registry()}/{repo}@{digest}"


def build_and_push_image(dockerfile_path: str, ecr_repo: str,
                         build_args: dict | None = None) -> dict:
    # Tag with the short commit for traceability; pin by digest afterward.
    import os
    short_sha = os.environ.get("COMMIT_SHA", "latest")[:12] or "latest"
    tag = f"git-{short_sha}"
    image_uri = f"{_registry()}/{ecr_repo}:{tag}"

    try:
        _do_build(dockerfile_path, image_uri, build_args)
    except subprocess.CalledProcessError as e:
        return {"error": "build/push failed", "stderr": e.stderr[-2000:]}

    image_ref = _resolve_digest(ecr_repo, tag)
    return {"image_ref": image_ref, "tag": tag, "repo": ecr_repo}
