def test_imports():
    import dify_plugin  # noqa
    from mirage.cli.client import make_client, DaemonClient  # noqa
    from mirage.config import load_config  # noqa
    import httpx, uvicorn  # noqa


def test_build_ram_config_dict():
    from mirage.config import load_config

    cfg = load_config({"mode": "WRITE", "mounts": {"/data": {"resource": "ram"}}}, env={})
    d = cfg.to_workspace_kwargs()
    assert "cache" in d or "mode" in d  # sanity: config validates & dumps
    assert cfg.model_dump(mode="json")["mounts"]["/data"]["resource"] == "ram"
