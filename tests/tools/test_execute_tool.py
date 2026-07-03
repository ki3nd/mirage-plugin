import pytest
from dify_plugin.entities.tool import ToolInvokeMessage

from tools.execute import ExecuteTool
from tools._daemon import MANAGER


def _tool(conv="c1", env="", cache_backend="ram", redis_url=None):
    t = ExecuteTool.__new__(ExecuteTool)

    class R:
        credentials = {"env": env, "cache_backend": cache_backend, "redis_url": redis_url}

    class S:
        conversation_id = conv
        message_id = None

    t.runtime = R()
    t.session = S()
    # __init__ is @final on the SDK base Tool and normally sets this; since
    # we bypass it via __new__ (per task-8-brief, to invoke _invoke without a
    # full plugin runtime) we set it manually so create_text_message/
    # create_json_message (which read self.response_type) work for real.
    t.response_type = ToolInvokeMessage
    return t


@pytest.fixture(autouse=True)
def _teardown():
    yield
    MANAGER.shutdown()


def test_execute_tool_ram_write_then_read():
    t = _tool()
    msgs = list(t._invoke({
        "workspace_yaml": "mode: WRITE\nmounts:\n  /data: {resource: ram}\n",
        "command": "echo hi > /data/x && cat /data/x",
    }))
    assert any("hi" in str(getattr(m, "message", m)) for m in msgs)


def test_execute_tool_yields_text_then_json():
    t = _tool(conv="c2")
    msgs = list(t._invoke({
        "workspace_yaml": "mode: WRITE\nmounts:\n  /data: {resource: ram}\n",
        "command": "echo hello",
    }))
    assert len(msgs) == 2
    assert msgs[0].type == ToolInvokeMessage.MessageType.TEXT
    assert "hello" in msgs[0].message.text
    assert msgs[1].type == ToolInvokeMessage.MessageType.JSON
    payload = msgs[1].message.json_object
    assert payload["exit_code"] == 0
    assert payload["command"] == "echo hello"
    assert "hello" in payload["stdout"]


def test_execute_tool_default_read_only_blocks_write():
    t = _tool(conv="c3")
    msgs = list(t._invoke({
        "workspace_yaml": "mounts:\n  /data: {resource: ram}\n",
        "command": "echo hi > /data/x",
    }))
    payload = msgs[-1].message.json_object
    assert payload["exit_code"] != 0


def test_execute_tool_bad_config_yields_error_text_without_raising():
    t = _tool(conv="c4")
    msgs = list(t._invoke({
        "workspace_yaml": "mounts: [this is not, valid: :: yaml",
        "command": "echo hi",
    }))
    assert len(msgs) == 1
    assert msgs[0].type == ToolInvokeMessage.MessageType.TEXT
