"""Approval record store (DynamoDB).

Schema (table: cd-agent-approvals), partition key = request_id (S):
    request_id    : str   (uuid)
    status        : str   ('pending' | 'approved' | 'rejected')
    task_def_arn  : str   (artifact this approval is bound to)
    service       : str
    commit_sha    : str
    summary       : str
    created_at    : int   (epoch seconds)
    decided_at    : int   (epoch seconds, set when approved/rejected)
    decided_by    : str   (who approved, set by the webhook)

CRITICAL: the agent path only ever READS this table (get_approval). Only the
human-driven approval webhook writes status='approved'/'rejected'. Grant the
agent's IAM role dynamodb:GetItem only; grant the webhook role PutItem/UpdateItem.
"""
import time
import uuid

import boto3

from agent import config

_ddb = boto3.resource("dynamodb", region_name=config.AWS_REGION)
_table = _ddb.Table(config.APPROVALS_TABLE)


def create_pending(service: str, task_def_arn: str, commit_sha: str,
                   summary: str) -> str:
    """Create a pending approval request. Returns request_id."""
    request_id = str(uuid.uuid4())
    _table.put_item(
        Item={
            "request_id": request_id,
            "status": "pending",
            "service": service,
            "task_def_arn": task_def_arn,
            "commit_sha": commit_sha,
            "summary": summary,
            "created_at": int(time.time()),
        },
        ConditionExpression="attribute_not_exists(request_id)",
    )
    return request_id


def get_approval(request_id: str) -> dict | None:
    """Read an approval record. Returns None if not found."""
    resp = _table.get_item(Key={"request_id": request_id})
    return resp.get("Item")


def decide(request_id: str, approved: bool, decided_by: str) -> dict:
    """Record a human decision. Called ONLY by the approval webhook.

    Uses a conditional update so a request can only be decided once and only
    from the 'pending' state (prevents replay / double-decision races).
    """
    new_status = "approved" if approved else "rejected"
    resp = _table.update_item(
        Key={"request_id": request_id},
        UpdateExpression="SET #s = :new, decided_at = :t, decided_by = :who",
        ConditionExpression="#s = :pending",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":new": new_status,
            ":pending": "pending",
            ":t": int(time.time()),
            ":who": decided_by,
        },
        ReturnValues="ALL_NEW",
    )
    return resp["Attributes"]
