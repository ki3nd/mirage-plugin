"""Tool `snapshot`: export a Mirage workspace as a downloadable tar archive.

Mirrors ``tools.execute`` wiring (parse_env_block -> build_config_dict /
make_workspace_id -> DaemonManager), but instead of running a command it
snapshots the workspace: the daemon writes a tar to disk and
``DaemonManager.snapshot`` reads it back as bytes (gzip-compressed client
side when requested). Snapshots capture mount configs, touched file bytes,
sessions and history; live-only resources (Slack/Gmail) and untouched files
are not captured.

Any failure is reported back as a text message instead of raising, so a
single bad tool call never crashes the plugin process.
"""

from dify_plugin import Tool

from tools._config import build_config_dict, make_workspace_id
from tools._daemon import MANAGER
from tools._env import parse_env_block, redact_secrets


class SnapshotTool(Tool):
    def _invoke(self, tool_parameters: dict):
        env: dict = {}
        try:
            workspace_yaml = tool_parameters["workspace_yaml"]
            compress = tool_parameters.get("compress") or "none"
            compress_arg = None if compress == "none" else compress

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
            blob = MANAGER.snapshot(wid, compress=compress_arg)
        except Exception as e:
            yield self.create_text_message(
                redact_secrets(f"mirage snapshot failed: {e}", env))
            return

        if compress_arg == "gz":
            filename, mime = "workspace.tar.gz", "application/gzip"
        else:
            filename, mime = "workspace.tar", "application/x-tar"
        yield self.create_blob_message(blob, meta={"mime_type": mime, "filename": filename})
