# Mirage — Dify Plugin

Run bash commands across S3, Slack, Redis, RAM, and more — as one unified virtual filesystem — from inside Dify.

This plugin embeds [Mirage](https://docs.mirage.strukto.ai), a unified virtual filesystem for AI agents. It mounts external resources (object storage, chat, databases, local scratch space, …) at filesystem paths, so an agent can `ls`, `cat`, `grep`, and pipe across all of them with plain bash — no per-service SDK or custom vocabulary.

## Tools

| Tool | What it does |
|------|--------------|
| **execute** | Run a bash command inside a Mirage workspace. Returns `stdout`, `stderr`, and `exit_code`. Supports pipes and cross-resource operations, e.g. `grep -r alert /slack/general \| wc -l` or `cp /s3/report.csv /data/`. |
| **snapshot** | Export the current workspace as a downloadable tar archive (optionally gzip-compressed). Captures mount configs, touched file bytes, sessions, and history. |

## How it works

- The plugin runs the Mirage engine in a **local daemon process** (`127.0.0.1`) and talks to it over HTTP. The daemon holds live connections and a warm read cache, so repeated commands hit local state instead of re-connecting to remote services on every call.
- Each **conversation gets its own workspace** (keyed by the workspace config together with the conversation), so parallel conversations stay isolated. Within a conversation the workspace is reused. Idle workspaces are evicted automatically.
- Secrets never appear in tool parameters or in anything the model sees — they live only in the plugin credentials and are injected locally.

## Configuration

### Provider credentials (set once, encrypted)

| Field | Description |
|-------|-------------|
| **Secrets (.env)** | `KEY=VALUE` per line (UPPERCASE keys), e.g. `AWS_ACCESS_KEY_ID=...`. Referenced from the workspace YAML as `${KEY}`. |
| **Cache backend** | `ram` (default) or `redis`. |
| **Redis URL** | Required only when the cache backend is `redis`, e.g. `redis://localhost:6379/0`. |

### Workspace YAML (a tool parameter)

Each tool call takes a `workspace_yaml` that declares the mounts. Secrets are referenced by name (`${UPPERCASE}`) and resolved from the credentials above — never inline the actual secret value.

```yaml
mode: WRITE                       # optional; omit for read-only (see Safety)
mounts:
  /data:
    resource: ram
  /s3:
    resource: s3
    config:
      bucket: logs
      aws_access_key_id: ${AWS_ACCESS_KEY_ID}
      aws_secret_access_key: ${AWS_SECRET_ACCESS_KEY}
  /slack:
    resource: slack
    config:
      token: ${SLACK_BOT_TOKEN}
```

Use block style (indented), not inline `{ ... }` flow style: a `${SECRET}` placeholder inside `{ }` is not valid YAML and will fail to parse.

Then, for example, call **execute** with `command: "grep -r alert /slack | wc -l"`.

## Safety — read-only by default

A workspace is **read-only unless you opt in**. Commands that write, modify, or delete fail unless the YAML sets `mode: WRITE` at the top level (whole workspace) or on an individual mount. Enable writes only when the task actually requires creating, editing, or deleting something.

## Requirements

- Python 3.12.
- The plugin starts a local daemon subprocess and binds a `127.0.0.1` port, so it must run in an environment that permits spawning a subprocess and local networking (e.g. a self-hosted Dify deployment).

## Development

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest        # tests run against a locally-spawned daemon
```

Design docs and the implementation plan live under `docs/`.
