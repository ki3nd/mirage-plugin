# Privacy Policy

## What this plugin does

The mirage plugin runs a local **mirage daemon** on `127.0.0.1` (localhost) inside
the same host as the Dify plugin runtime. Each tool call sends your workspace
configuration to that daemon over localhost only, and the daemon executes the
requested command against the resources you mount.

## Data we collect

The plugin itself does **not** collect, store, or transmit any personal data to
the plugin author or to any third-party service.

## Data you provide, and where it goes

- **Credentials / secrets** (the `env` block, `redis_url`, etc.) are stored by
  Dify as provider credentials. At invocation time they are interpolated into
  your workspace configuration and sent **only** to the local `127.0.0.1`
  daemon. They are never sent anywhere else by the plugin.
- **Workspace YAML and commands** you pass as tool parameters are sent only to
  the local daemon.
- **Resource traffic**: The daemon connects to exactly the resources your
  workspace YAML mounts (e.g. S3, Slack, Redis, HuggingFace). Data leaving the
  host is limited to the network calls those mounts require, governed by each
  provider's own terms and privacy policy — not by this plugin.

## Data retention

- Workspaces (and any RAM cache they hold) live in the daemon subprocess only.
  They are evicted after an idle period and are lost when the daemon exits.
  Nothing is persisted by the plugin beyond the daemon's runtime.
- If you configure a **Redis** cache backend, cached data persists in the Redis
  instance you point to, under your control and subject to your Redis
  configuration.
- Error messages returned to the model have secret values redacted before they
  leave the plugin.

## Third parties

This plugin does not share data with the plugin author or any third party. Any
external service is one **you** explicitly mount in your workspace YAML; review
that service's privacy policy for how it handles your data.

## Contact

For questions about this policy, contact the plugin author (`ki3nd`).
