"""Webhook server: receives GitHub pushes and Slack approval callbacks.

Endpoints:
  POST /github/webhook  -> verifies signature, kicks off run_deploy
  POST /slack/actions   -> verifies signature, records decision, resumes agent

Both verify HMAC signatures before doing anything. Run behind TLS. The agent
runs are dispatched to a background worker (here: a thread / task) so webhooks
return fast; in production use SQS + a worker, or Step Functions.
"""
import hashlib
import hmac
import json
import os
import threading

from fastapi import FastAPI, Request, HTTPException

from agent import loop
from agent import approvals

app = FastAPI()

GITHUB_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "").encode()
SLACK_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "").encode()
WATCH_BRANCH = os.environ.get("WATCH_BRANCH", "refs/heads/main")


def _verify_github(body: bytes, sig_header: str) -> bool:
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(GITHUB_SECRET, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


@app.post("/github/webhook")
async def github_webhook(request: Request):
    body = await request.body()
    if not _verify_github(body, request.headers.get("X-Hub-Signature-256", "")):
        raise HTTPException(status_code=401, detail="bad signature")

    if request.headers.get("X-GitHub-Event") != "push":
        return {"ignored": "not a push event"}

    payload = json.loads(body)
    if payload.get("ref") != WATCH_BRANCH:
        return {"ignored": f"branch {payload.get('ref')} not watched"}

    event = {
        "sha": payload["after"],
        "ref": payload["ref"],
        "repo": payload["repository"]["full_name"],
        "pusher": payload.get("pusher", {}).get("name", ""),
        "message": payload.get("head_commit", {}).get("message", ""),
        "run_id": payload["after"][:12],
    }
    # NOTE: a real deployment must checkout the repo at event['sha'] into
    # config.WORKSPACE before the agent runs (a build sidecar / init step).
    threading.Thread(target=loop.run_deploy, args=(event,), daemon=True).start()
    return {"status": "accepted", "run_id": event["run_id"]}


def _verify_slack(body: bytes, ts: str, sig: str) -> bool:
    if not ts or not sig:
        return False
    basestring = b"v0:" + ts.encode() + b":" + body
    expected = "v0=" + hmac.new(SLACK_SECRET, basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


@app.post("/slack/actions")
async def slack_actions(request: Request):
    body = await request.body()
    if not _verify_slack(
        body,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        request.headers.get("X-Slack-Signature", ""),
    ):
        raise HTTPException(status_code=401, detail="bad signature")

    # Slack sends application/x-www-form-urlencoded with a 'payload' field.
    from urllib.parse import parse_qs
    form = parse_qs(body.decode())
    payload = json.loads(form["payload"][0])

    action = payload["actions"][0]
    value = json.loads(action["value"])  # {request_id, run_id, sha}
    approved = action["action_id"] == "approve_deploy"
    user = payload["user"]["username"]

    # Record the human decision (idempotent; only transitions from pending).
    try:
        rec = approvals.decide(value["request_id"], approved, decided_by=user)
    except Exception:
        return {"text": "This request was already decided."}

    if approved:
        threading.Thread(
            target=loop.resume_after_approval,
            args=(value["run_id"], value["request_id"], value["sha"]),
            daemon=True,
        ).start()
        return {"text": f":white_check_mark: Approved by {user}. Deploying to prod."}
    return {"text": f":x: Rejected by {user}. No prod deploy."}
