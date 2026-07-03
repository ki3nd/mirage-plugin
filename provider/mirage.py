from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from tools._env import parse_env_block


class MirageProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        try:
            raw = credentials.get("env")
            if not raw or not raw.strip():
                raise ValueError("Secrets (.env) is required")
            parse_env_block(raw)
            if credentials.get("cache_backend") == "redis":
                if not (credentials.get("redis_url") or "").strip():
                    raise ValueError("redis_url is required when cache backend is redis")
        except Exception as e:
            raise ToolProviderCredentialValidationError(str(e))
