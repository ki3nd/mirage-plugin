import gzip

import pytest
from dify_plugin.entities.tool import ToolInvokeMessage

from tools.snapshot import SnapshotTool
from tools._daemon import MANAGER

WRITE_YAML = "mode: WRITE\nmounts:\n  /data: {resource: ram}\n"


def _tool(conv="s1", env="", cache_backend="ram", redis_url=None):
    t = SnapshotTool.__new__(SnapshotTool)

    class R:
        credentials = {"env": env, "cache_backend": cache_backend, "redis_url": redis_url}

    class S:
        conversation_id = conv
        message_id = None

    t.runtime = R()
    t.session = S()
    # Tool.__init__ is @final and normally sets response_type; bypassed via
    # __new__ so create_blob_message/create_text_message work for real.
    t.response_type = ToolInvokeMessage
    return t


@pytest.fixture(autouse=True)
def _teardown():
    yield
    MANAGER.shutdown()


def _blobs(msgs):
    return [m for m in msgs if m.type == ToolInvokeMessage.MessageType.BLOB]


def test_snapshot_tool_returns_tar_blob():
    t = _tool()
    msgs = list(t._invoke({"workspace_yaml": WRITE_YAML, "compress": "none"}))
    blobs = _blobs(msgs)
    assert len(blobs) == 1
    assert isinstance(blobs[0].message.blob, bytes) and len(blobs[0].message.blob) > 0


def test_snapshot_tool_gz():
    t = _tool(conv="s2")
    msgs = list(t._invoke({"workspace_yaml": WRITE_YAML, "compress": "gz"}))
    blob = _blobs(msgs)[0].message.blob
    assert blob[:2] == b"\x1f\x8b"
    assert len(gzip.decompress(blob)) > 0


def test_snapshot_tool_bad_yaml_yields_error_text():
    t = _tool(conv="s3")
    msgs = list(t._invoke({"workspace_yaml": "mounts: [not, valid: :: yaml", "compress": "none"}))
    assert len(msgs) == 1
    assert msgs[0].type == ToolInvokeMessage.MessageType.TEXT
