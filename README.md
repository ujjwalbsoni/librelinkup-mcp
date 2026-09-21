# librelinkup-mcp

An MCP server that exposes [LibreLinkUp](https://www.librelinkup.com/) glucose
data (via [`pylibrelinkup`](https://github.com/robberwick/pylibrelinkup)) as
tools for any MCP-compatible client (Claude Desktop, Claude Code, etc).

LibreLinkUp itself has no public API keys — this works the same way the
LibreLinkUp mobile app does: you sign in with the email/password of a
LibreLinkUp account that has been added as a **follower** of one or more
FreeStyle Libre users.

## Tools

| Tool | Description |
|---|---|
| `list_patients()` | List everyone you follow, with their `patient_id` and name. |
| `get_current_glucose(patient)` | Latest reading, high/low flags, trend arrow. |
| `get_glucose_graph(patient)` | ~last 12 hours of readings. |
| `get_glucose_logbook(patient)` | ~last 2 weeks of logged events. |

`patient` accepts either the `patient_id` (UUID) or the `"First Last"` name
returned by `list_patients()`.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Set your LibreLinkUp credentials as environment variables (do **not**
hard-code them anywhere):

```bash
export LIBRELINKUP_EMAIL="you@example.com"
export LIBRELINKUP_PASSWORD="your_password"
# Optional — defaults to US. One of: US, EU, EU2, DE, FR, JP, AP, AU, AE, CA, LA, RU
export LIBRELINKUP_REGION="US"
```

Run it directly to sanity-check:

```bash
python server.py
```

## Connecting a client

### Claude Desktop / Claude Code (`claude_desktop_config.json` or `.mcp.json`)

```json
{
  "mcpServers": {
    "librelinkup": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/server.py"],
      "env": {
        "LIBRELINKUP_EMAIL": "you@example.com",
        "LIBRELINKUP_PASSWORD": "your_password",
        "LIBRELINKUP_REGION": "US"
      }
    }
  }
}
```

Restart the client after saving the config. Then try asking it something like
"What's my latest glucose reading?" — it will call `list_patients` (if
needed) and `get_current_glucose` on your behalf.

## Notes

- The underlying client authenticates once per process and is cached; if a
  call fails with an auth error the server re-authenticates once and retries.
- LibreLinkUp accounts are region-locked (the same account/region used in the
  LibreLinkUp app). If `list_patients()` returns an empty list or an auth
  error, double-check `LIBRELINKUP_REGION`.
- This talks to LibreLinkUp's unofficial-but-widely-used follower API via
  `pylibrelinkup`; it is not affiliated with or endorsed by Abbott.
- Credentials never leave environment variables passed to the process — the
  server does not log or persist them.

## Troubleshooting Guide

- Verify that your LibreLinkUp account has API access enabled and at least one linked sensor.
- Ensure the pylibrelinkup client (v0.10.0+) sends the Authorization: Bearer * and Account‑Id: <sha256(user-id)> headers; if not, update the library or manually add those headers in your code.
- If you see 400 Bad Request – missing or malformed jwt`, double‑check credentials, region, and that 2FA is disabled or you’re using an app‑specific password.

That’s the only specific guidance needed.
