import os

# Simulate the real plugin process: dify_plugin patches gevent at import.
# Do this BEFORE anything spawns the daemon so the client side is tested under gevent.
import dify_plugin  # noqa: F401,E402

# Point the daemon client at a dedicated TEST port so tests never touch a real daemon.
# Verified: mirage.cli.settings.load_daemon_settings() reads MIRAGE_DAEMON_URL
# (mirage.cli.env.ENV_DAEMON_URL == "MIRAGE_DAEMON_URL").
os.environ.setdefault("MIRAGE_DAEMON_URL", "http://127.0.0.1:8799")
