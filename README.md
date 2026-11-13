# garmin-mcp

A remote MCP server for your own Garmin Connect data, for Claude and ChatGPT on web, desktop and mobile. Read-only.

The server holds no secrets. Every request must carry a key Garmin accepts, or it gets a `401` before reaching MCP. Each client sends your Garmin Personal API key as `Authorization: Bearer <key>`; the server exchanges it for a 24-hour Garmin access token and reads your data with Garmin's official [`garmin-connect-personal-api-python`](https://github.com/garmin/connect-personal-api-python) client.

## Tools

One tool per endpoint in Garmin's Personal API spec (26), with the spec's parameters and defaults:

- **Activities:** `search_activities` (every search filter), `activity`, `activity_workouts`, `download_activity`
- **Daily wellness:** `daily_user_summary`, `daily_summaries`, `steps`, `stress`, `hydration`, `body_battery`, `heart_rate`, `spo2`, `wellness_activity_summaries`
- **Sleep and HRV:** `sleep`, `sleep_summaries`, `hrv`, `hrv_summaries`
- **Fitness:** `fitness_age`, `heart_rate_zones`
- **Weight and nutrition:** `weight_summaries`, `weight_entries`, `weight_goals`, `food_logs`
- **Women's health:** `menstrual_cycle_summary`, `menstrual_cycle_day_view`
- **Profile:** `profile_settings`

`download_activity` decodes the original FIT file with Garmin's FIT SDK and returns every named message type (session, laps, events, devices, zones...) plus the per-record time series, downsampled to one record per `record_interval_seconds` (default 30; 0 keeps all). Per-second duplicates (`hr_mesgs`, `gps_metadata`) and message types missing from the public FIT profile are counted but not returned; beat-to-beat HRV is opt-in.

## Get a key

Garmin Connect (web) → Personal API Key → Create Key. Pick the 1-year expiry. The key is shown once.

## Connect

URL: `https://<your-deployment>/mcp`

- **Claude** (claude.ai → Settings → Connectors → Add custom connector): paste the URL, then under **Request headers** add `Authorization` = `Bearer <your key>`.
- **ChatGPT** (Settings → Apps & Connectors → Advanced → Developer mode, then Create): paste the URL, choose **Token** authentication, and paste the key.
- **Claude Code**: `claude mcp add --transport http garmin https://<your-deployment>/mcp --header "Authorization: Bearer <your key>"`

When the key expires, create a new one and update it in each client.

## Develop

```sh
uv sync
uv run uvicorn app:app --reload
```

Deployed on Vercel's Python runtime; `app.py` exposes the ASGI `app`.
