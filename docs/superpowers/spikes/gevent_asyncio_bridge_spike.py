import dify_plugin  # noqa: F401  -> triggers gevent monkey.patch_all
import gevent
import asyncio
import asyncio_gevent

asyncio.set_event_loop_policy(asyncio_gevent.EventLoopPolicy())

# one persistent gevent-backed asyncio loop for the whole process,
# driven by a background GREENLET (not an OS thread) so it cooperates with the hub
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)
print("loop type:", type(LOOP).__module__, type(LOOP).__name__)

from mirage import Workspace
from mirage.config import load_config
from mirage.cache.file.config import CacheConfig
from mirage.cache.index.config import IndexConfig


def build_ws():
    cfg = load_config({"mode": "WRITE", "mounts": {"/data": {"resource": "ram"}}}, env={})
    kw = cfg.to_workspace_kwargs()
    kw["cache"] = CacheConfig(limit="128MB")
    kw["index"] = IndexConfig()
    return Workspace(**kw)


import gevent.lock
LOCK = gevent.lock.BoundedSemaphore(1)


def run_coro(coro):
    with LOCK:  # serialize: only one run_until_complete at a time across the process
        return LOOP.run_until_complete(coro)


def do_exec(ws, cmd):
    r = run_coro(ws.execute(cmd))
    o = run_coro(r.stdout_str())
    r.sync_exit_code()
    return o.strip(), r.exit_code


def main():
    ws = build_ws()
    # call 1 write, call 2 read on SAME ws -> proves persistence/reuse across calls
    _, c1 = do_exec(ws, "echo hi > /data/x")
    out2, c2 = do_exec(ws, "cat /data/x")
    print(f"REUSE: write_exit={c1} read_out={out2!r} read_exit={c2}")

    # concurrency: two executes in parallel greenlets on the same ws
    g1 = gevent.spawn(do_exec, ws, "echo aaa")
    g2 = gevent.spawn(do_exec, ws, "echo bbb")
    gevent.joinall([g1, g2], timeout=20)
    print(f"CONCURRENT: g1={g1.value} g2={g2.value}")

    run_coro(ws.close())
    print("OK ALL DONE")


main()
