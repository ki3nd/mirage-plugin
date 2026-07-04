# Mirage — Dify Plugin

**Author:** [ki3nd](https://github.com/ki3nd)  
**Type:** Tool  
**Github Repo:** https://github.com/ki3nd/mirage-plugin  
**Github Issues:** https://github.com/ki3nd/mirage-plugin/issues

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

Paste your secrets into the **Secrets (.env)** field, one `KEY=VALUE` per line.
These are stored encrypted by Dify and are the only place actual secret values
should ever live:

```dotenv
# Secrets (.env) — provider credentials
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
SLACK_BOT_TOKEN=...
```

Each line becomes a `${KEY}` you can reference from the workspace YAML — for the
example above, `${AWS_ACCESS_KEY_ID}`, `${AWS_SECRET_ACCESS_KEY}`, and
`${SLACK_BOT_TOKEN}`. If you set **Cache backend** to `redis`, also fill in the
**Redis URL** field (e.g. `redis://localhost:6379/0`).

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

### Resource setup

Each mount's `resource` and `config` fields follow Mirage's own resource specs.
For the full list of supported backends and how to configure each one (required
credentials, options, mount modes), see the Mirage docs:

- **Resource matrix (all backends):** https://docs.mirage.strukto.ai/home/resource-matrix
- **Per-resource setup guides:** https://docs.mirage.strukto.ai/home/setup/ — e.g.
  [S3](https://docs.mirage.strukto.ai/home/setup/s3),
  [Slack](https://docs.mirage.strukto.ai/home/setup/slack),
  [GitHub](https://docs.mirage.strukto.ai/home/setup/github),
  [Postgres](https://docs.mirage.strukto.ai/home/setup/postgres),
  [Notion](https://docs.mirage.strukto.ai/home/setup/notion),
  [Hugging Face Datasets](https://docs.mirage.strukto.ai/home/setup/hf_datasets),
  and more.

Put secret values in the provider credentials and reference them from the YAML
as `${UPPERCASE}` — don't paste the actual credential into `config`.

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
