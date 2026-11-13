"""Remote MCP server for your own Garmin Connect data.

Clients authenticate by sending their Garmin Personal API key as
`Authorization: Bearer <key>`. The server stores no secrets: it exchanges the
key for a 24h Garmin access token per request (cached in /tmp) and reads data.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from garmin_personal_api import GarminPersonalApiClient, HttpError, PersonalApiAuthenticator
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer(
    "garmin",
    instructions="Read-only access to the user's Garmin Connect health and activity data. Dates are YYYY-MM-DD.",
)

# /tmp is the only writable path on Vercel; the cache is keyed by a hash of the API key.
authenticator = PersonalApiAuthenticator(bearer_token_cache_file="/tmp/garmin-bearer-token.json")


@contextmanager
def garmin(ctx: Context) -> Iterator[GarminPersonalApiClient]:
    headers = ctx.headers or {}
    authorization = headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise ToolError(
            "Missing Garmin Personal API key. Configure the connector with the header "
            "`Authorization: Bearer <your Garmin Personal API key>`."
        )
    api_key = authorization.removeprefix("Bearer ").strip()
    try:
        token = authenticator.obtain_token(api_key)
    except HttpError as error:
        raise ToolError(
            f"Garmin rejected the API key (HTTP {error.status_code}). It may be wrong, expired or deleted."
        ) from error
    with GarminPersonalApiClient(token.access_token) as client:
        try:
            yield client
        except HttpError as error:
            raise ToolError(f"Garmin API error (HTTP {error.status_code}): {error.response_text[:500]}") from error


@mcp.tool()
def daily_summaries(start_date: str, end_date: str, ctx: Context) -> Any:
    """Daily totals (steps, calories, distance, stress, body battery, resting HR, intensity minutes) for an inclusive date range."""
    with garmin(ctx) as client:
        return client.get_daily_summaries(start_date, end_date)


@mcp.tool()
def sleep(calendar_date: str, ctx: Context) -> Any:
    """Detailed sleep for one night (stages, score, timing), keyed by the date you woke up."""
    with garmin(ctx) as client:
        return client.get_daily_sleep(calendar_date)


@mcp.tool()
def sleep_summaries(start_date: str, end_date: str, ctx: Context) -> Any:
    """Per-night sleep summaries for an inclusive date range."""
    with garmin(ctx) as client:
        return client.get_daily_sleep_summaries(start_date, end_date)


@mcp.tool()
def hrv_summaries(start_date: str, end_date: str, ctx: Context) -> Any:
    """Nightly heart rate variability (HRV) summaries and status for an inclusive date range."""
    with garmin(ctx) as client:
        return client.get_daily_hrv_summaries(start_date, end_date)


@mcp.tool()
def body_battery(start_date: str, end_date: str, ctx: Context) -> Any:
    """Daily Body Battery charge/drain for an inclusive date range."""
    with garmin(ctx) as client:
        return client.get_daily_body_battery(start_date, end_date)


@mcp.tool()
def stress(start_date: str, end_date: str, ctx: Context) -> Any:
    """Daily stress for an inclusive date range."""
    with garmin(ctx) as client:
        return client.get_daily_stress(start_date, end_date)


@mcp.tool()
def steps(start_date: str, end_date: str, ctx: Context) -> Any:
    """Daily step counts and goals for an inclusive date range."""
    with garmin(ctx) as client:
        return client.get_daily_steps(start_date, end_date)


@mcp.tool()
def heart_rate(calendar_date: str, ctx: Context) -> Any:
    """Heart rate for one day, including resting HR and the intraday series."""
    with garmin(ctx) as client:
        return client.get_daily_heart_rate(calendar_date)


@mcp.tool()
def fitness(calendar_date: str, ctx: Context) -> Any:
    """Fitness age and heart rate zones as of a date."""
    with garmin(ctx) as client:
        return {
            "fitness_age": client.get_fitness_age(calendar_date),
            "heart_rate_zones": client.get_heart_rate_zones(),
        }


@mcp.tool()
def weight(start_date: str, end_date: str, ctx: Context) -> Any:
    """Weight and body composition measurements for an inclusive date range."""
    with garmin(ctx) as client:
        return client.get_weight_measurements(start_date, end_date)


@mcp.tool()
def activities(
    ctx: Context,
    start_date: str | None = None,
    end_date: str | None = None,
    activity_type: str | None = None,
    limit: int = 20,
) -> Any:
    """Search recorded activities (runs, rides, workouts...), newest first. Filter by date range and Garmin activity type key such as "running"."""
    with garmin(ctx) as client:
        return client.search_activities(
            limit=limit, start_date=start_date, end_date=end_date, activity_type=activity_type
        )


@mcp.tool()
def activity(activity_id: int, ctx: Context) -> Any:
    """Full details for one activity: splits, pace, heart rate, training effect."""
    with garmin(ctx) as client:
        return client.get_activity(activity_id)


# Serving on 0.0.0.0 turns off the SDK's localhost-only DNS-rebinding guard, which would reject the public host.
app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
