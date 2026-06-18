"""Smoke test tool: verify a service is healthy after deploy."""
import urllib.request

from agent import config

_TIMEOUT = 10
_RETRIES = 5
_BACKOFF = 6  # seconds between attempts


def run_smoke_test(service: str, env: str) -> dict:
    endpoint = config.SMOKE_ENDPOINTS.get((service, env))
    if not endpoint:
        return {"result": "skipped", "reason": "no endpoint configured",
                "service": service, "env": env}

    import time
    last_err = None
    for attempt in range(1, _RETRIES + 1):
        try:
            req = urllib.request.Request(endpoint, method="GET")
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                code = resp.getcode()
                body = resp.read(2048).decode("utf-8", "ignore")
            if 200 <= code < 300:
                return {"result": "pass", "service": service, "env": env,
                        "status_code": code, "attempt": attempt}
            last_err = f"HTTP {code}: {body[:200]}"
        except Exception as e:
            last_err = str(e)
        time.sleep(_BACKOFF)

    return {"result": "fail", "service": service, "env": env,
            "error": last_err, "attempts": _RETRIES}
