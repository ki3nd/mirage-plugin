import pytest

from tools._daemon import DaemonManager
from tools._config import build_config_dict, make_workspace_id

RAM_W = "mode: WRITE\nmounts:\n  /data: {resource: ram}\n"


@pytest.fixture
def mgr():
    m = DaemonManager()
    m.ensure_daemon()
    yield m
    m.shutdown()  # DELETE tracked workspaces + stop reaper (Task 5); daemon self-exits when empty


def test_execute_reuse(mgr):
    cfg = build_config_dict(RAM_W, env={})
    wid = make_workspace_id(RAM_W, {}, "ram", None, "conv1")
    mgr.ensure_workspace(wid, cfg)
    _, _, c1 = mgr.execute(wid, "echo hi > /data/x")
    out, _, c2 = mgr.execute(wid, "cat /data/x")   # reuse: read what write left
    assert c1 == 0 and c2 == 0 and out.strip() == "hi"


def test_default_read_blocks_write(mgr):
    ro = "mounts:\n  /data: {resource: ram}\n"
    cfg = build_config_dict(ro, env={})              # no mode -> READ
    wid = make_workspace_id(ro, {}, "ram", None, "convRO")
    mgr.ensure_workspace(wid, cfg)
    _, _, code = mgr.execute(wid, "echo hi > /data/x")
    assert code != 0


def test_conversation_isolation(mgr):
    cfg = build_config_dict(RAM_W, env={})
    w1 = make_workspace_id(RAM_W, {}, "ram", None, "A"); mgr.ensure_workspace(w1, cfg)
    w2 = make_workspace_id(RAM_W, {}, "ram", None, "B"); mgr.ensure_workspace(w2, cfg)
    mgr.execute(w1, "echo one > /data/f")
    out, _, code = mgr.execute(w2, "cat /data/f")    # separate workspace -> file absent
    assert code != 0                                  # isolated (no such file)
