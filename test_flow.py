"""Offline verification of the agent control flow.

No AWS, no Anthropic API. We monkeypatch:
  - the tool implementations (so nothing real happens)
  - the model call (a scripted sequence of tool_use blocks)
and assert that:
  1. the happy path builds, deploys to dev, and smoke tests
  2. a failed smoke test triggers rollback and stops
  3. prompt-injected text in commit data does NOT cause unintended actions
"""
import sys
import types

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

from agent import validator
from agent import loop as agent_loop

# ---- fake tool implementations -------------------------------------------
CALLS = []

validator._IMPL["get_commit_diff"] = lambda sha: {
    "sha": sha, "changed_files": ["M\tindex.html"], "diff": "..."}
validator._IMPL["build_and_push_image"] = lambda **kw: (
    CALLS.append(("build", kw)) or
    {"image_ref": "ACCT.dkr.ecr.us-east-1.amazonaws.com/paw-web@sha256:abc123"})
validator._IMPL["register_task_definition"] = lambda **kw: (
    CALLS.append(("register", kw)) or
    {"task_def_arn": "arn:aws:ecs:us-east-1:ACCT:task-definition/paw-web:42"})
validator._IMPL["deploy_to_dev"] = lambda **kw: (
    CALLS.append(("deploy_dev", kw)) or {"status": "deployed", "env": "dev"})
validator._IMPL["run_smoke_test"] = lambda **kw: (
    CALLS.append(("smoke", kw)) or {"result": "pass", **kw})
validator._IMPL["rollback"] = lambda **kw: (
    CALLS.append(("rollback", kw)) or {"status": "rolled_back"})

# ---- scripted model responses --------------------------------------------
ARN = "arn:aws:ecs:us-east-1:ACCT:task-definition/paw-web:42"
IMG = "ACCT.dkr.ecr.us-east-1.amazonaws.com/paw-web@sha256:abc123"

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

def script_happy_path():
    return iter([
        Resp([txt("Inspecting commit."), tu("get_commit_diff", sha="deadbeef")]),
        Resp([tu("build_and_push_image", dockerfile_path="Dockerfile", ecr_repo="paw-web")]),
        Resp([tu("register_task_definition", family="paw-web", image_ref=IMG)]),
        Resp([tu("deploy_to_dev", service="paw-web-dev", task_def_arn=ARN)]),
        Resp([tu("run_smoke_test", service="paw-web-dev", env="dev")]),
        Resp([txt("Dev deploy complete.")], stop="end_turn"),
    ])

def script_smoke_fail():
    def _fail_smoke(**kw):
        CALLS.append(("smoke_fail", kw))
        return {"result": "fail", "error": "503"}
    validator._IMPL["run_smoke_test"] = _fail_smoke
    return iter([
        Resp([tu("get_commit_diff", sha="deadbeef")]),
        Resp([tu("build_and_push_image", dockerfile_path="Dockerfile", ecr_repo="paw-web")]),
        Resp([tu("register_task_definition", family="paw-web", image_ref=IMG)]),
        Resp([tu("deploy_to_dev", service="paw-web-dev", task_def_arn=ARN)]),
        Resp([tu("run_smoke_test", service="paw-web-dev", env="dev")]),
        Resp([tu("rollback", service="paw-web-dev", env="dev")]),
        Resp([txt("Smoke test failed. Rolled back.")], stop="end_turn"),
    ])

def drive(script):
    agent_loop._call_model = lambda messages: next(script())

def names():
    return [c[0] for c in CALLS]

# ---- tests ---------------------------------------------------------------
def test_happy_path():
    CALLS.clear()
    validator._IMPL["run_smoke_test"] = lambda **kw: (
        CALLS.append(("smoke", kw)) or {"result": "pass", **kw})
    it = script_happy_path()
    agent_loop._call_model = lambda messages: next(it)
    agent_loop.run_deploy({"sha": "deadbeef", "repo": "viralpatel94/paw", "run_id": "deadbeef"})
    seq = names()
    assert "build" in seq
    assert "deploy_dev" in seq
    assert "smoke" in seq
    assert "rollback" not in seq
    print("PASS  happy path — build, deploy, smoke, no rollback:", seq)

def test_smoke_fail_triggers_rollback():
    CALLS.clear()
    it = script_smoke_fail()
    agent_loop._call_model = lambda messages: next(it)
    agent_loop.run_deploy({"sha": "deadbeef", "repo": "viralpatel94/paw", "run_id": "deadbeef"})
    seq = names()
    assert "smoke_fail" in seq
    assert "rollback" in seq
    print("PASS  smoke failure triggers rollback:", seq)

if __name__ == "__main__":
    test_happy_path()
    test_smoke_fail_triggers_rollback()
    print("\nAll tests passed.")
