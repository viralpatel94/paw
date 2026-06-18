"""Build & push tool using plain Docker (available in GitHub Actions)."""
import base64
import json
import os
import subprocess

import boto3

from agent import config


def _ecr_client():
    return boto3.client("ecr", region_name=config.AWS_REGION)


def _registry() -> str:
    return f"{config.AWS_ACCOUNT_ID}.dkr.ecr.{config.AWS_REGION}.amazonaws.com"


def _ecr_login():
    tok = _ecr_client().get_authorization_token()["authorizationData"][0]
    user, pwd = base64.b64decode(tok["authorizationToken"]).decode().split(":", 1)
    subprocess.run(
        ["docker", "login", "--username", user, "--password-stdin", _registry()],
        input=pwd, text=True, check=True, capture_output=True,
    )


def _do_build(dockerfile_path: str, image_uri: str, build_args: dict | None):
    _ecr_login()
    cmd = ["docker", "build", "-t", image_uri,
           "-f", os.path.join(config.WORKSPACE, dockerfile_path),
           config.WORKSPACE]
    for k, v in (build_args or {}).items():
        cmd += ["--build-arg", f"{k}={v}"]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    subprocess.run(["docker", "push", image_uri], check=True, capture_output=True, text=True)


def _resolve_digest(repo: str, tag: str) -> str:
    resp = _ecr_client().describe_images(
        repositoryName=repo, imageIds=[{"imageTag": tag}]
    )
    digest = resp["imageDetails"][0]["imageDigest"]
    return f"{_registry()}/{repo}@{digest}"


def build_and_push_image(dockerfile_path: str, ecr_repo: str,
                         build_args: dict | None = None) -> dict:
    short_sha = os.environ.get("COMMIT_SHA", "latest")[:12] or "latest"
    tag = f"git-{short_sha}"
    image_uri = f"{_registry()}/{ecr_repo}:{tag}"

    try:
        _do_build(dockerfile_path, image_uri, build_args)
    except subprocess.CalledProcessError as e:
        return {"error": "build/push failed", "stderr": e.stderr[-2000:]}

    image_ref = _resolve_digest(ecr_repo, tag)
    return {"image_ref": image_ref, "tag": tag, "repo": ecr_repo}
