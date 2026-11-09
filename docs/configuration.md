# Configuration Reference

All configuration is via environment variables, validated at startup with
Pydantic.

## Environment variables

### Server

| Variable | Default | Description |
| --- | --- | --- |
| `CALDAV_MCP_PORT` | `8080` | Listen port (inside container) |
| `CALDAV_MCP_PATH` | `/mcp` | Streamable HTTP endpoint path |
| `CALDAV_MCP_API_KEY` | `""` (disabled) | Shared secret for MCP endpoint auth |
| `CALDAV_MCP_READ_ONLY` | `false` | Hide write tools; only query tools are exposed when `true` |
| `CALDAV_MCP_MASK_EVENTS` | `false` | Mask event details before they reach MCP clients (see below) |
| `CALDAV_MCP_KEEP_TITLES` | `false` | With masking enabled, keep real event titles instead of `masked_event` |
| `CALDAV_MCP_CALENDAR_WHITELIST` | `""` (all) | Comma-separated calendar names; only these are listed and accessible |
| `CALDAV_MCP_CONFIG_SECRET` | `""` | Master secret for encrypting CalDAV passwords at rest in the SQLite store. Required by the CLI (encrypt) and server (decrypt). Changing it invalidates stored ciphertexts. |
| `CALDAV_MCP_DB_PATH` | `""` | Path to the SQLite configuration store. Required by the CLI and by the server in pro mode; `--db` CLI flag takes precedence. |
| `DB_CONFIG_ENABLED` | `false` | Enable pro mode: load config and users from the SQLite store; requires `CALDAV_MCP_DB_PATH` and `CALDAV_MCP_CONFIG_SECRET`; `CALDAV_MCP_API_KEY` is ignored. |
| `TZ` | `""` (UTC) | IANA timezone (e.g. `Europe/Vienna`) for today/week boundaries |

### CalDAV

| Variable | Default | Description |
| --- | --- | --- |
| `CALDAV_URL` | `""` | CalDAV server URL. When set, request headers are ignored and all credentials come from env (environment mode). When unset, `X-Caldav-*` headers are required (header mode). |
| `CALDAV_USERNAME` | `""` | CalDAV username. Used in environment mode; ignored in header mode. |
| `CALDAV_PASSWORD` | `""` | CalDAV password. Used in environment mode; ignored in header mode. |
| `CALDAV_MCP_CALDAV_VERIFY_SSL` | `true` | Verify TLS certs on CalDAV connections. Set `false` only for testing with self-signed certs. |

### TLS

| Variable | Default | Description |
| --- | --- | --- |
| `CALDAV_MCP_TLS_CERT` | `""` | Path to TLS certificate PEM file |
| `CALDAV_MCP_TLS_KEY` | `""` | Path to TLS private key PEM file |
| `CALDAV_MCP_TLS_CA_BUNDLE` | `""` | Optional CA bundle for custom certificate authorities |

### Rate limiting

| Variable | Default | Description |
| --- | --- | --- |
| `CALDAV_MCP_RATE_LIMIT_MAX_FAILURES` | `10` | Max failed auth attempts per IP within the sliding window |
| `CALDAV_MCP_RATE_LIMIT_WINDOW_SECONDS` | `60` | Sliding window duration in seconds |

### Logging

| Variable | Default | Description |
| --- | --- | --- |
| `CALDAV_MCP_LOG_FORMAT` | `text` | Audit log format: `text` or `json` |

### Event masking

`CALDAV_MCP_MASK_EVENTS=true` strips event details before they are returned to
an MCP client: the summary is replaced with `masked_event`, and location,
description, categories, and attendees are emptied. Start/end times and the
event UID are preserved so tools remain addressable by UID.

Set `CALDAV_MCP_KEEP_TITLES=true` as well to keep real event titles (and times)
while still hiding every other field. It has no effect unless
`CALDAV_MCP_MASK_EVENTS` is also enabled.

Masking is applied at the serialization boundary, so it covers every read tool
— `caldav_get_events`, `caldav_get_today_events`, `caldav_get_week_events`,
`caldav_get_event_by_uid`, `caldav_search_events`, `caldav_get_freebusy`, and
`caldav_list_attendees` — in both simple and pro (fan-out) mode. Search runs
against the masked data, so it cannot be used to recover hidden details.

The unprefixed aliases `MASK_EVENTS` and `KEEP_TITLES` are also accepted; the
`CALDAV_MCP_*` names win when both are set.

> Masking hides data from reads only. Write tools still accept a UID and can
> modify the real event. Pair masking with `CALDAV_MCP_READ_ONLY=true` if the
> model must not be able to change events it cannot see.

### Calendar whitelist

`CALDAV_MCP_CALENDAR_WHITELIST` accepts a comma-separated list of calendar
names (whitespace trimmed). When non-empty, it is enforced everywhere:

- `caldav_list_calendars` returns only whitelisted calendars.
- Any tool that selects a calendar by name — reads, writes, attendees, moves —
  rejects non-whitelisted names with `not_found`, exactly as if the calendar
  did not exist. The "available" hint only lists whitelisted calendars.
- The default calendar (when no name is given) is the first *whitelisted*
  calendar.

In pro mode the whitelist is intersected with the calendars declared in the
SQLite config: `caldav_list_calendars` shows only the calendars configured for
each remote, and access still respects the env whitelist.

Leave the variable empty (the default) to expose every calendar on the account.
The unprefixed alias `CALENDAR_WHITELIST` is also accepted; the
`CALDAV_MCP_*` name wins when both are set.

> Calendar names are matched exactly (case-sensitive), so use the same names
> that `caldav_list_calendars` reports before enabling the whitelist.

## Deployment patterns

### Install from a release

```bash
git clone --branch v0.1.0 https://github.com/gelse/caldav-mcp.git
cd caldav-mcp
cp .env.example .env
# Edit .env with your CalDAV credentials
docker compose up -d
```

### Local / private deployment

The simplest setup — AI client and caldav-mcp on the same machine:

```
AI Client → http://localhost:8600/mcp → CalDAV Server
```

```bash
docker compose up -d
```

The server listens on `localhost:8600` and is not accessible from the
network unless you explicitly publish the port.

### Remote / shared deployment

For multi-user or remote access, put the server behind a TLS-terminating
reverse proxy:

```
AI Client → HTTPS → reverse proxy → caldav-mcp → CalDAV Server
```

```nginx
server {
    listen 443 ssl;
    server_name caldav-mcp.example.com;

    ssl_certificate     /etc/ssl/certs/caldav-mcp.pem;
    ssl_certificate_key /etc/ssl/private/caldav-mcp-key.pem;

    location /mcp {
        proxy_pass http://127.0.0.1:8600/mcp;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Built-in TLS

If you prefer not to use a reverse proxy, enable built-in TLS:

```bash
CALDAV_MCP_TLS_CERT=/path/to/cert.pem \
CALDAV_MCP_TLS_KEY=/path/to/key.pem \
docker compose up -d
```

> ⚠️ **Do not expose the MCP endpoint publicly without both authentication
> and TLS.** Without `CALDAV_MCP_API_KEY` set, the endpoint is open. Without
> TLS, all traffic — including API keys and CalDAV passwords — is transmitted
> in plaintext.

## Deployment recommendations

- Bind to `127.0.0.1` or a private network unless you need remote access.
- Restrict access at the network/firewall layer to trusted hosts or a VPN.
- Never commit CalDAV app passwords to version control.
- Use a reverse proxy for TLS termination in production.
- For read-only deployments, set `CALDAV_MCP_READ_ONLY=true`.
