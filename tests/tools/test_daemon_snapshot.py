import gzip

import pytest

from tools._config import build_config_dict, make_workspace_id
from tools._daemon import DaemonManager

RAM_W = "mode: WRITE\nmounts:\n  /data: {resource: ram}\n"


@pytest.fixture
def mgr():
    m = DaemonManager()
    m.ensure_daemon()
    yield m
    m.shutdown()


def test_snapshot_tar_bytes(mgr):
    cfg = build_config_dict(RAM_W, env={})
    wid = make_workspace_id(RAM_W, {}, "ram", None, "snap")
    mgr.ensure_workspace(wid, cfg)
    mgr.execute(wid, "echo hi > /data/x")
    blob = mgr.snapshot(wid)
    assert isinstance(blob, bytes) and len(blob) > 0


def test_snapshot_gz(mgr):
    cfg = build_config_dict(RAM_W, env={})
    wid = make_workspace_id(RAM_W, {}, "ram", None, "snapgz")
    mgr.ensure_workspace(wid, cfg)
    blob = mgr.snapshot(wid, compress="gz")
    assert blob[:2] == b"\x1f\x8b" and len(gzip.decompress(blob)) > 0
