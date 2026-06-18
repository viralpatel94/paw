"""Webhook server: receives GitHub pushes and approval callbacks.

Endpoints:
  POST /github/webhook  -> verifies signature, kicks off run_deploy
  POST /approve/{request_id}  -> simple HTTP approval endpoint (replace with
                                  your own auth layer in production)

Both verify HMAC signatures or bearer tokens before doing anything. Run behind TLS.
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
WATCH_BRANCH = os.environ.get("WATCH_BRANCH", "refs/heads/main")
APPROVAL_TOKEN = os.environ.get("APPROVAL_TOKEN", "")  # bearer token for /approve


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
    threading.Thread(target=loop.run_deploy, args=(event,), daemon=True).start()
    return {"status": "accepted", "run_id": event["run_id"]}


@app.post("/approve/{request_id}")
async def approve(request_id: str, request: Request):
    """Approve or reject a pending prod deploy.

    Body: {"approved": true, "decided_by": "your-name"}
    Header: Authorization: Bearer <APPROVAL_TOKEN>
    """
    auth = request.headers.get("Authorization", "")
    if APPROVAL_TOKEN and auth != f"Bearer {APPROVAL_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")

    body = await request.json()
    approved = bool(body.get("approved"))
    decided_by = body.get("decided_by", "api")

    try:
        rec = approvals.decide(request_id, approved, decided_by=decided_by)
    except Exception as e:
        raise HTTPException(status_code=409, detail=str(e))

    if approved:
        run_id = rec.get("run_id", request_id[:12])
        sha = rec.get("commit_sha", "")
        threading.Thread(
            target=loop.resume_after_approval,
            args=(run_id, request_id, sha),
            daemon=True,
        ).start()
        return {"status": "approved", "request_id": request_id}
    return {"status": "rejected", "request_id": request_id}
