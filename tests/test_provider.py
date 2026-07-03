import pytest
from dify_plugin.errors.tool import ToolProviderCredentialValidationError
from provider.mirage import MirageProvider


def _p():
    return MirageProvider.__new__(MirageProvider)


def test_ok():
    _p()._validate_credentials({"env": "A=1"})


def test_redis_needs_url():
    with pytest.raises(ToolProviderCredentialValidationError):
        _p()._validate_credentials({"env": "A=1", "cache_backend": "redis"})


def test_redis_ok():
    _p()._validate_credentials({"env": "A=1", "cache_backend": "redis", "redis_url": "redis://h:6379/0"})
