"""Slack notifier: posts an approval request with Approve / Reject buttons.

The button 'value' carries everything the approval webhook needs to resume the
agent. The actual authority lives in the approvals table, not in this message.
"""
import json
import os
import urllib.request

SLACK_WEBHOOK_URL = os.environ.get("SLACK_APPROVAL_WEBHOOK_URL", "")


def post_approval_request(request_id, service, task_def_arn, summary):
    if not SLACK_WEBHOOK_URL:
        return
    sha = os.environ.get("COMMIT_SHA", "")
    run_id = sha[:12]
    value = json.dumps({"request_id": request_id, "run_id": run_id, "sha": sha})

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": "Prod deploy approval needed"}},
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Service:* `{service}`\n*Task def:* `{task_def_arn}`\n\n{summary}"}},
        {"type": "actions", "elements": [
            {"type": "button", "style": "primary",
             "text": {"type": "plain_text", "text": "Approve"},
             "action_id": "approve_deploy", "value": value},
            {"type": "button", "style": "danger",
             "text": {"type": "plain_text", "text": "Reject"},
             "action_id": "reject_deploy", "value": value},
        ]},
    ]
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=json.dumps({"blocks": blocks}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10)
