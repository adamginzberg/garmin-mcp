"""Remote MCP server for your own Garmin Connect data.

Clients authenticate by sending their Garmin Personal API key as
`Authorization: Bearer <key>`. The server stores no secrets: it exchanges the
key for a 24h Garmin access token (cached per key in /tmp) and reads data.
"""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import anyio.to_thread
from garmin_personal_api import BearerToken, GarminPersonalApiClient, HttpError, PersonalApiAuthenticator
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

mcp = MCPServer(
    "garmin",
    instructions="Read-only access to the user's Garmin Connect health and activity data. Dates are YYYY-MM-DD.",
)

def api_key_from(authorization: str | None) -> str | None:
    if authorization is None or not authorization.startswith("Bearer "):
        return None
    return authorization.removeprefix("Bearer ").strip() or None


def obtain_token(api_key: str) -> BearerToken:
    # The library's cache file holds a single token, so give each key its own file; Fluid
    # instances serve many requests and would otherwise thrash it. /tmp is Vercel's only writable path.
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    cache_file = f"/tmp/garmin-tokens/{key_hash}.json"
    with PersonalApiAuthenticator(bearer_token_cache_file=cache_file) as authenticator:
        return authenticator.obtain_token(api_key)


@contextmanager
def garmin(ctx: Context) -> Iterator[GarminPersonalApiClient]:
    headers = ctx.headers or {}
    api_key = api_key_from(headers.get("authorization"))
    if api_key is None:
        raise RuntimeError("require_garmin_key should have rejected a request without a key")
    try:
        token = obtain_token(api_key)
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


def unauthorized(message: str) -> JSONResponse:
    return JSONResponse(
        {"error": "unauthorized", "message": message},
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer realm="garmin-mcp"'},
    )


def require_garmin_key(app: ASGIApp) -> ASGIApp:
    """Reject any HTTP request that doesn't carry a Garmin key Garmin accepts, before it reaches MCP."""

    async def guarded(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        api_key = api_key_from(Headers(scope=scope).get("authorization"))
        if api_key is None:
            response = unauthorized("Send your Garmin Personal API key as `Authorization: Bearer <key>`.")
            await response(scope, receive, send)
            return
        try:
            await anyio.to_thread.run_sync(obtain_token, api_key)
        except HttpError as error:
            if error.status_code not in (400, 401, 403):
                raise
            response = unauthorized("Garmin rejected the API key. It may be wrong, expired or deleted.")
            await response(scope, receive, send)
            return
        await app(scope, receive, send)

    return guarded


# Serving on 0.0.0.0 turns off the SDK's localhost-only DNS-rebinding guard, which would reject the public host.
mcp_app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
app = require_garmin_key(mcp_app)
