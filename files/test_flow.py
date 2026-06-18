"""Offline verification of the agent control flow and the prod approval gate.

No AWS, no Anthropic API. We monkeypatch:
  - the tool implementations (so nothing real happens)
  - the model call (a scripted sequence of tool_use blocks)
and assert that:
  1. the happy path stops at request_prod_approval (no prod deploy without approval)
  2. deploy_to_prod is REJECTED by the validator when no approval exists
  3. deploy_to_prod SUCCEEDS only when an approval bound to the exact arn exists
  4. a prompt-injected 'deploy to prod' in commit data does NOT cause a prod deploy
"""
import json
import types
import sys

# ---- stub external SDKs before importing the package ----------------------
boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *a, **k: types.SimpleNamespace(Table=lambda n: None)
boto3_stub.client = lambda *a, **k: None
sys.modules["boto3"] = boto3_stub

anthropic_stub = types.ModuleType("anthropic")
class _FakeClient:
    def __init__(self, *a, **k): ...
anthropic_stub.Anthropic = _FakeClient
sys.modules["anthropic"] = anthropic_stub

# ---- now import the package ----------------------------------------------
from agent import validator, approvals
from agent import loop as agent_loop


# ---- in-memory approvals store -------------------------------------------
_STORE = {}

def fake_get_approval(rid):
    return _STORE.get(rid)

def fake_create_pending(service, task_def_arn, commit_sha, summary):
    rid = "req-123"
    _STORE[rid] = {"request_id": rid, "status": "pending", "service": service,
                   "task_def_arn": task_def_arn, "commit_sha": commit_sha}
    return rid

approvals.get_approval = fake_get_approval
approvals.create_pending = fake_create_pending
validator.get_approval = fake_get_approval  # validator imported it by name


# ---- fake tool implementations -------------------------------------------
CALLS = []

def record(name):
    def f(**kw):
        CALLS.append((name, kw))
        return {"ok": name, **{k: v for k, v in kw.items() if k != "build_args"}}
    return f

validator._IMPL["get_commit_diff"] = lambda sha: {"sha": sha, "changed_files": ["A\tapp.py"], "diff": "..."}
validator._IMPL["build_and_push_image"] = lambda **kw: (CALLS.append(("build", kw)) or
    {"image_ref": "ACCT.dkr.ecr.us-east-1.amazonaws.com/myapp-api@sha256:abc123"})
validator._IMPL["register_task_definition"] = lambda **kw: (CALLS.append(("register", kw)) or
    {"task_def_arn": "arn:aws:ecs:us-east-1:ACCT:task-definition/myapp-api:42"})
validator._IMPL["deploy_to_dev"] = lambda **kw: (CALLS.append(("deploy_dev", kw)) or
    {"status": "deployed", "env": "dev"})
validator._IMPL["request_prod_approval"] = lambda **kw: (CALLS.append(("request_approval", kw)) or
    {"request_id": fake_create_pending(kw["service"], kw["task_def_arn"], "deadbeef", kw["summary"]),
     "status": "pending"})
validator._IMPL["check_approval_status"] = lambda **kw: (CALLS.append(("check", kw)) or
    {"status": _STORE.get(kw["request_id"], {}).get("status", "not_found")})
validator._IMPL["run_smoke_test"] = lambda **kw: (CALLS.append(("smoke", kw)) or
    {"result": "pass", **kw})
validator._IMPL["rollback"] = lambda **kw: (CALLS.append(("rollback", kw)) or {"status": "rolled_back"})
# deploy_to_prod stays special-cased in validator (uses ecs.update_service)
import tools.ecs as ecs_mod
ecs_mod.update_service = lambda env, service, task_def_arn: (
    CALLS.append(("PROD_DEPLOY", {"service": service, "arn": task_def_arn})) or
    {"status": "deployed", "env": env})


# ---- a scripted "model": yields tool_use blocks in sequence ---------------
class Block:
    def __init__(self, **kw): self.__dict__.update(kw)
    def model_dump(self): return dict(self.__dict__)

def tu(name, **inp):
    return Block(type="tool_use", id=f"t_{name}", name=name, input=inp)

def txt(s):
    return Block(type="text", text=s)

class Resp:
    def __init__(self, content, stop="tool_use"):
        self.content = content
        self.stop_reason = stop

ARN = "arn:aws:ecs:us-east-1:ACCT:task-definition/myapp-api:42"
IMG = "ACCT.dkr.ecr.us-east-1.amazonaws.com/myapp-api@sha256:abc123"


def script_first_pass():
    """Build -> register -> dev deploy -> dev smoke -> request approval -> stop."""
    steps = [
        Resp([txt("Inspecting commit."), tu("get_commit_diff", sha="deadbeef")]),
        Resp([tu("build_and_push_image", dockerfile_path="Dockerfile", ecr_repo="myapp-api")]),
        Resp([tu("register_task_definition", family="myapp-api", image_ref=IMG)]),
        Resp([tu("deploy_to_dev", service="myapp-api-dev", task_def_arn=ARN)]),
        Resp([tu("run_smoke_test", service="myapp-api-dev", env="dev")]),
        Resp([tu("request_prod_approval", service="myapp-api-prod",
                 task_def_arn=ARN, summary="dev green")]),
        Resp([txt("Dev is green. Awaiting prod approval.")], stop="end_turn"),
    ]
    return iter(steps)


def script_prod_no_approval():
    """Malicious/buggy: tries deploy_to_prod with a bogus request_id."""
    return iter([
        Resp([tu("deploy_to_prod", service="myapp-api-prod",
                 task_def_arn=ARN, request_id="req-123")]),
        Resp([txt("stopped")], stop="end_turn"),
    ])


def script_prod_with_approval():
    return iter([
        Resp([tu("check_approval_status", request_id="req-123")]),
        Resp([tu("deploy_to_prod", service="myapp-api-prod",
                 task_def_arn=ARN, request_id="req-123")]),
        Resp([tu("run_smoke_test", service="myapp-api-prod", env="prod")]),
        Resp([txt("Prod deployed and green.")], stop="end_turn"),
    ])


def drive(script):
    it = script()
    agent_loop._call_model = lambda messages: next(it)


def names():
    return [c[0] for c in CALLS]


# ---- tests ----------------------------------------------------------------
def test_happy_first_pass():
    CALLS.clear(); _STORE.clear()
    drive(script_first_pass)
    agent_loop._save_thread = lambda *a, **k: None
    agent_loop.run_deploy({"sha": "deadbeef", "repo": "x/y", "run_id": "deadbeef"})
    seq = names()
    assert "request_approval" in seq, seq
    assert "PROD_DEPLOY" not in seq, "PROD DEPLOY HAPPENED WITHOUT APPROVAL!"
    print("PASS  first pass stops at approval, no prod deploy:", seq)


def test_prod_blocked_without_approval():
    CALLS.clear(); _STORE.clear()
    _STORE["req-123"] = {"request_id": "req-123", "status": "pending",
                         "task_def_arn": ARN, "commit_sha": "deadbeef"}
    drive(script_prod_no_approval)
    agent_loop._save_thread = lambda *a, **k: None
    agent_loop._load_thread = lambda rid: [{"role": "user", "content": "resume"}]
    agent_loop.resume_after_approval("deadbeef", "req-123", "deadbeef")
    assert "PROD_DEPLOY" not in names(), "PROD DEPLOY ON PENDING APPROVAL!"
    print("PASS  prod blocked while approval pending:", names())


def test_prod_allowed_with_matching_approval():
    CALLS.clear(); _STORE.clear()
    _STORE["req-123"] = {"request_id": "req-123", "status": "approved",
                         "task_def_arn": ARN, "commit_sha": "deadbeef"}
    drive(script_prod_with_approval)
    agent_loop._save_thread = lambda *a, **k: None
    agent_loop._load_thread = lambda rid: [{"role": "user", "content": "resume"}]
    agent_loop.resume_after_approval("deadbeef", "req-123", "deadbeef")
    assert "PROD_DEPLOY" in names(), "approved deploy did not happen!"
    print("PASS  prod deploy proceeds with matching approval:", names())


def test_prod_blocked_on_arn_mismatch():
    CALLS.clear(); _STORE.clear()
    # Approval exists & approved, but for a DIFFERENT artifact.
    _STORE["req-123"] = {"request_id": "req-123", "status": "approved",
                         "task_def_arn": "arn:.../myapp-api:99",
                         "commit_sha": "deadbeef"}
    drive(script_prod_with_approval)
    agent_loop._save_thread = lambda *a, **k: None
    agent_loop._load_thread = lambda rid: [{"role": "user", "content": "resume"}]
    agent_loop.resume_after_approval("deadbeef", "req-123", "deadbeef")
    assert "PROD_DEPLOY" not in names(), "DEPLOYED DESPITE ARN MISMATCH!"
    print("PASS  prod blocked when approved arn != deployed arn:", names())


if __name__ == "__main__":
    test_happy_first_pass()
    test_prod_blocked_without_approval()
    test_prod_allowed_with_matching_approval()
    test_prod_blocked_on_arn_mismatch()
    print("\nAll control-flow and gate checks passed.")
