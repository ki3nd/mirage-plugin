import dify_plugin  # noqa: F401 -> gevent monkey.patch_all (simulate plugin process)
import gevent.monkey
print("threading patched:", gevent.monkey.is_module_patched("threading"))

import json
from mirage.cli.client import make_client
from mirage.config import load_config


def main():
    client = make_client()
    print("daemon url:", client.settings.url)
    # (a) spawn daemon subprocess + reach it via sync httpx (both under gevent)
    client.ensure_running(startup_timeout=40.0)
    print("STEP a: daemon reachable =", client.is_reachable())

    # (b) create a RAM workspace with a deterministic id
    wid = "spike-conv1-cfgHASH"
    cfg = load_config({"mode": "WRITE", "mounts": {"/data": {"resource": "ram"}}}, env={})
    r = client.request("POST", "/v1/workspaces",
                       json={"config": cfg.model_dump(mode="json"), "id": wid})
    print("STEP b: create ->", r.status_code, (r.text[:200] if r.status_code >= 400 else "ok"))

    # (c) execute two commands on the SAME workspace -> proves reuse/persistence
    def ex(cmd):
        rr = client.request("POST", f"/v1/workspaces/{wid}/execute",
                            json={"command": cmd})
        return rr.status_code, rr.json() if rr.status_code < 400 else rr.text

    s1, o1 = ex("echo hi > /data/x")
    s2, o2 = ex("cat /data/x")
    print("STEP c1 write:", s1, json.dumps(o1)[:200])
    print("STEP c2 read :", s2, json.dumps(o2)[:200])

    # cleanup: remove workspace + shut daemon down
    client.request("DELETE", f"/v1/workspaces/{wid}")
    try:
        client.request("POST", "/v1/shutdown", timeout=5)
    except Exception as e:
        print("shutdown note:", type(e).__name__)
    print("OK ALL DONE")


main()
