"""Build a mirage WorkspaceConfig dict (for POST /v1/workspaces) and derive
a deterministic workspace id from the inputs that shape it.

Schema notes (verified against the installed mirage.config module):
- ``WorkspaceConfig.cache``/``.index`` are discriminated unions keyed by a
  ``type`` field with literal values ``"ram"``/``"redis"`` (RamCacheBlock /
  RedisCacheBlock / RamIndexBlock / RedisIndexBlock in mirage/config.py, all
  ``extra="forbid"``). The dict shapes built below match those blocks
  exactly and were round-tripped through ``WorkspaceConfig.model_validate``.
- ``WorkspaceConfig.mode`` is a ``MountMode`` str-Enum with *lowercase*
  values ("read"/"write"/"exec"). ``cfg.model_dump(mode="json")`` therefore
  emits ``"read"``/``"write"`` (not the "READ"/"WRITE" casing used in the
  YAML), even though the coercion validator accepts either case on input.
- ``MountBlock.config`` is a plain ``dict[str, Any]`` -- there is no
  ``SecretStr`` anywhere in this schema, so ``${VAR}`` placeholders that get
  interpolated (client-side, before validation) show up as plaintext in
  ``model_dump(mode="json")``. Nothing redacts them.
"""

import hashlib

import yaml
from mirage.config import load_config


def build_config_dict(workspace_yaml: str, env: dict[str, str],
                      cache_backend: str = "ram",
                      redis_url: str | None = None,
                      cache_limit: str = "128MB") -> dict:
    """Parse workspace YAML, force a safe default mode, resolve secrets via
    ``env``, and override the cache/index blocks for the requested backend.

    Args:
        workspace_yaml (str): raw workspace config YAML text.
        env (dict[str, str]): resolved env mapping for ``${VAR}`` interpolation.
        cache_backend (str): "ram" (default) or "redis".
        redis_url (str | None): required when ``cache_backend == "redis"``.
        cache_limit (str): cache size limit, e.g. "128MB".

    Returns:
        dict: JSON-mode dump of the validated ``WorkspaceConfig``, with
        ``cache``/``index`` overridden to match ``cache_backend``. Safe to
        POST directly to the daemon's ``/v1/workspaces`` endpoint.

    Raises:
        ValueError: ``cache_backend == "redis"`` but no ``redis_url`` given.
    """
    raw = yaml.safe_load(workspace_yaml) or {}
    if isinstance(raw, dict):
        raw.setdefault("mode", "READ")
    cfg = load_config(raw, env=env)
    d = cfg.model_dump(mode="json")
    if cache_backend == "redis":
        if not redis_url:
            raise ValueError("cache_backend=redis requires redis_url")
        d["cache"] = {"type": "redis", "url": redis_url, "limit": cache_limit}
        d["index"] = {"type": "redis", "url": redis_url}
    else:
        d["cache"] = {"type": "ram", "limit": cache_limit}
        d["index"] = {"type": "ram"}
    return d


def make_workspace_id(workspace_yaml: str, env: dict[str, str],
                      cache_backend: str, redis_url: str | None,
                      conversation_id: str) -> str:
    """Derive a stable, conversation-scoped workspace id.

    Hashes the raw inputs that determine workspace identity/config
    (YAML text, env mapping, cache backend, redis url, conversation id)
    rather than the validated/dumped config, so the id still varies with
    ``env`` even if a future schema change were to redact secrets from
    ``model_dump``.

    Args:
        workspace_yaml (str): raw workspace config YAML text.
        env (dict[str, str]): resolved env mapping.
        cache_backend (str): "ram" or "redis".
        redis_url (str | None): redis URL, if any.
        conversation_id (str): conversation/session scoping key.

    Returns:
        str: ``"ws-" + sha256(...).hexdigest()[:32]``.
    """
    h = hashlib.sha256()
    h.update(workspace_yaml.encode()); h.update(b"\0")
    for k in sorted(env):
        h.update(f"{k}={env[k]}".encode()); h.update(b"\0")
    h.update((cache_backend or "ram").encode()); h.update(b"\0")
    h.update((redis_url or "").encode()); h.update(b"\0")
    h.update((conversation_id or "global").encode())
    return "ws-" + h.hexdigest()[:32]
