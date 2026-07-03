"""Tool `execute`: run a bash command against a Mirage workspace.

Wires together the pieces built in earlier tasks:
- ``tools._env.parse_env_block`` -- turn the provider's raw ".env" secret
  block into a ``{KEY: VALUE}`` mapping used to resolve ``${KEY}``
  placeholders in the workspace YAML.
- ``tools._config.build_config_dict`` / ``make_workspace_id`` -- validate
  the workspace YAML into a daemon-ready config dict (forcing safe
  READ-only defaults) and derive a stable, conversation-scoped workspace id
  so repeated calls within the same conversation reuse the same workspace.
- ``tools._daemon.MANAGER`` -- the module-level ``DaemonManager`` singleton
  that spawns/reaches the daemon and creates/executes against workspaces.

Any failure (bad YAML/config, daemon unreachable, HTTP error, timeout) is
reported back to the LLM as a text message instead of raising, so a single
bad tool call never crashes the plugin process.
"""

from dify_plugin import Tool

from tools._config import build_config_dict, make_workspace_id
from tools._daemon import MANAGER
from tools._env import parse_env_block, redact_secrets


class ExecuteTool(Tool):
    def _invoke(self, tool_parameters: dict):
        env: dict = {}
        try:
            workspace_yaml = tool_parameters["workspace_yaml"]
            command = tool_parameters["command"]
            creds = self.runtime.credentials
            env = parse_env_block(creds.get("env") or "")
            cache_backend = creds.get("cache_backend") or "ram"
            redis_url = creds.get("redis_url")

            conv = (
                getattr(self.session, "conversation_id", None)
                or getattr(self.session, "message_id", None)
                or "global"
            )

            config = build_config_dict(workspace_yaml, env, cache_backend, redis_url)
            wid = make_workspace_id(workspace_yaml, env, cache_backend, redis_url, conv)

            MANAGER.ensure_daemon()
            MANAGER.ensure_workspace(wid, config)
            # Pass config so a cold workspace (evicted / daemon respawned)
            # can be recreated transparently and the command retried once.
            out, err, code = MANAGER.execute(wid, command, config=config)
        except Exception as e:
            yield self.create_text_message(
                redact_secrets(f"mirage execute failed: {e}", env))
            return

        yield self.create_text_message(out or err)
        yield self.create_json_message({
            "command": command,
            "exit_code": code,
            "stdout": out,
            "stderr": err,
        })
