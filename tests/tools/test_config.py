from mirage.config import WorkspaceConfig

from tools._config import build_config_dict, make_workspace_id

RAM = "mounts:\n  /data: {resource: ram}\n"
RAM_W = "mode: WRITE\nmounts:\n  /data: {resource: ram}\n"


def test_default_mode_read():
    d = build_config_dict(RAM, env={})
    # NOTE: mirage.types.MountMode is a str-Enum with lowercase values
    # ("read"/"write"/"exec"). cfg.model_dump(mode="json") serializes the
    # enum via its .value, so the dumped dict carries "read", not "READ",
    # even though the raw YAML/default we feed in says "READ". Verified
    # directly against mirage.config.load_config().
    assert d["mode"] == "read"  # forced read-only when YAML omits mode


def test_explicit_write_kept():
    d = build_config_dict(RAM_W, env={})
    assert d["mode"] == "write"


def test_secret_interpolation():
    d = build_config_dict(
        'mounts:\n  /s3: {resource: s3, config: {bucket: b, aws_access_key_id: "${AK}", aws_secret_access_key: "${SK}"}}\n',
        env={"AK": "akid", "SK": "secret"})
    cfg_s3 = d["mounts"]["/s3"]["config"]
    # Verified: MountBlock.config is a plain dict[str, Any] (no SecretStr
    # field in the schema), so model_dump(mode="json") exposes the
    # already-interpolated plaintext value rather than a redacted one.
    assert cfg_s3["aws_access_key_id"] == "akid"
    assert cfg_s3["aws_secret_access_key"] == "secret"


def test_id_changes_with_env():
    # Companion to test_secret_interpolation per the brief's note: even if
    # model_dump had redacted secrets, the workspace id must still change
    # when env changes, since interpolation/hashing happens over the raw
    # inputs, not the (possibly redacted) validated output.
    a = make_workspace_id(RAM, {"AK": "one"}, "ram", None, "conv1")
    b = make_workspace_id(RAM, {"AK": "two"}, "ram", None, "conv1")
    assert a != b


def test_id_stable_and_conversation_scoped():
    a = make_workspace_id(RAM, {}, "ram", None, "conv1")
    b = make_workspace_id(RAM, {}, "ram", None, "conv1")
    c = make_workspace_id(RAM, {}, "ram", None, "conv2")
    assert a == b and a != c and a.startswith("ws-")


def test_ram_override_validates_back_through_workspace_config():
    d = build_config_dict(RAM, env={})
    assert d["cache"] == {"type": "ram", "limit": "128MB"}
    assert d["index"] == {"type": "ram"}
    cfg = WorkspaceConfig.model_validate(d)
    assert cfg.cache.type == "ram"
    assert cfg.index.type == "ram"


def test_redis_override_validates_back_through_workspace_config():
    d = build_config_dict(RAM, env={}, cache_backend="redis",
                          redis_url="redis://h:6379/0", cache_limit="64MB")
    assert d["cache"] == {"type": "redis", "url": "redis://h:6379/0", "limit": "64MB"}
    assert d["index"] == {"type": "redis", "url": "redis://h:6379/0"}
    cfg = WorkspaceConfig.model_validate(d)
    assert cfg.cache.type == "redis"
    assert cfg.cache.url == "redis://h:6379/0"
    assert cfg.index.type == "redis"


def test_redis_without_url_raises():
    import pytest
    with pytest.raises(ValueError):
        build_config_dict(RAM, env={}, cache_backend="redis")
