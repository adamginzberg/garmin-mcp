"""Remote MCP server for your own Garmin Connect data.

Clients authenticate by sending their Garmin Personal API key as
`Authorization: Bearer <key>`. The server stores no secrets: it exchanges the
key for a 24h Garmin access token (cached per key in /tmp) and reads data.

One tool per endpoint in Garmin's Personal API spec, with the spec's parameters and defaults.
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

from fit import parse_activity_archive

mcp = MCPServer(
    "garmin",
    instructions=(
        "Read-only access to the user's Garmin Connect health and activity data. "
        "Dates are YYYY-MM-DD in the user's local calendar. Range tools state their maximum span."
    ),
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


def query(params: dict[str, Any]) -> dict[str, Any]:
    # Omitted parameters fall back to Garmin's defaults; Java backends expect lowercase booleans.
    sent: dict[str, Any] = {}
    for name, value in params.items():
        if value is None:
            continue
        sent[name] = ("true" if value else "false") if isinstance(value, bool) else value
    return sent


def fetch(ctx: Context, path: str, params: dict[str, Any] | None = None) -> Any:
    with garmin(ctx) as client:
        return client.get(path, params=query(params or {}))


# --- Activities ---


@mcp.tool()
def search_activities(
    ctx: Context,
    start: int = 0,
    limit: int = 20,
    start_date: str | None = None,
    end_date: str | None = None,
    search: str | None = None,
    activity_type: str | None = None,
    activity_sub_type: str | None = None,
    excluded_activity_sub_types: list[str] | None = None,
    event_type: str | None = None,
    favorite: int | None = None,
    sort_by: str | None = None,
    sort_order: str | None = None,
    exclude_children: bool | None = None,
    min_distance_m: int | None = None,
    max_distance_m: int | None = None,
    min_duration_s: int | None = None,
    max_duration_s: int | None = None,
    min_elapsed_duration_s: int | None = None,
    max_elapsed_duration_s: int | None = None,
    min_elevation_gain_m: int | None = None,
    max_elevation_gain_m: int | None = None,
    min_avg_power_w: int | None = None,
    upper_latitude: float | None = None,
    lower_latitude: float | None = None,
    left_longitude: float | None = None,
    right_longitude: float | None = None,
    lower_max_depth_cm: int | None = None,
    upper_max_depth_cm: int | None = None,
    deco_dive: bool | None = None,
    min_bottom_time_s: int | None = None,
    max_bottom_time_s: int | None = None,
    required_fields: list[str] | None = None,
) -> Any:
    """Search activities, newest first by default, with paginated summaries.

    start/limit paginate (Garmin's default limit is 99). Dates filter on local start date.
    activity_type is a parent key such as running or cycling; activity_sub_type is a child key such as
    trail_running. event_type: race, training, recreation, fitness, specialEvent or uncategorized.
    favorite: 1 favorites only, 0 non-favorites. sort_by: e.g. name, distance, duration (default startLocal);
    sort_order: asc or desc. exclude_children (default true) hides legs of multisport activities.
    The bounding box filters on start location and needs all four coordinates.
    Depth, deco and bottom-time filters apply to dives. required_fields lists fields that must be non-null.
    """
    return fetch(
        ctx,
        "/activitylist-service/activities/search/activities",
        {
            "start": start,
            "limit": limit,
            "startDate": start_date,
            "endDate": end_date,
            "search": search,
            "activityType": activity_type,
            "activitySubType": activity_sub_type,
            "excludedActivitySubType": excluded_activity_sub_types,
            "eventType": event_type,
            "favorite": favorite,
            "sortBy": sort_by,
            "sortOrder": sort_order,
            "excludeChildren": exclude_children,
            "minDistance": min_distance_m,
            "maxDistance": max_distance_m,
            "minDuration": min_duration_s,
            "maxDuration": max_duration_s,
            "minElapsedDuration": min_elapsed_duration_s,
            "maxElapsedDuration": max_elapsed_duration_s,
            "minElevation": min_elevation_gain_m,
            "maxElevation": max_elevation_gain_m,
            "minAvgPower": min_avg_power_w,
            "upperLatitude": upper_latitude,
            "lowerLatitude": lower_latitude,
            "leftLongitude": left_longitude,
            "rightLongitude": right_longitude,
            "lowerMaxDepth": lower_max_depth_cm,
            "upperMaxDepth": upper_max_depth_cm,
            "decoDive": deco_dive,
            "minBottomTime": min_bottom_time_s,
            "maxBottomTime": max_bottom_time_s,
            "requiredField": required_fields,
        },
    )


@mcp.tool()
def activity(activity_id: int, ctx: Context) -> Any:
    """Summary, metadata, measurements and split summaries for one activity."""
    return fetch(ctx, f"/activity-service/activity/{activity_id}")


@mcp.tool()
def activity_workouts(activity_id: int, ctx: Context) -> Any:
    """Structured workouts performed during an activity."""
    return fetch(ctx, f"/activity-service/activity/{activity_id}/workouts")


@mcp.tool()
def download_activity(
    activity_id: int,
    ctx: Context,
    record_interval_seconds: int = 30,
    record_fields: list[str] | None = None,
    include_hrv: bool = False,
) -> Any:
    """The original activity file, decoded. For FIT files: every message type (session, laps, lengths,
    events, device info, sport, zones...) plus per-record time series (heart rate, speed, power, cadence,
    altitude, position, running dynamics...).

    Records are downsampled to one per record_interval_seconds (0 keeps every record, which can be huge).
    record_fields limits records to the named fields, e.g. ["heart_rate", "enhanced_speed", "power"].
    include_hrv adds beat-to-beat RR intervals when the watch recorded them. GPX/TCX uploads return text.
    """
    if record_interval_seconds < 0:
        raise ToolError("record_interval_seconds must be 0 or more")
    with garmin(ctx) as client:
        archive = client.download_activity(activity_id)
    return parse_activity_archive(archive, record_interval_seconds, record_fields, include_hrv)


# --- Daily wellness ---


@mcp.tool()
def daily_user_summary(calendar_date: str, ctx: Context) -> Any:
    """One day's wellness summary: steps, calories, stress, Body Battery and other metrics."""
    return fetch(ctx, "/usersummary-service/usersummary/daily", {"calendarDate": calendar_date})


@mcp.tool()
def daily_summaries(start_date: str, end_date: str, ctx: Context) -> Any:
    """Daily wellness summaries for a date range (max 1 year)."""
    return fetch(ctx, "/usersummary-service/v2/dailysummaries", {"startDate": start_date, "endDate": end_date})


@mcp.tool()
def steps(start_date: str, end_date: str, ctx: Context) -> Any:
    """Daily steps for a date range (max 28 days)."""
    return fetch(ctx, f"/usersummary-service/stats/steps/daily/{start_date}/{end_date}")


@mcp.tool()
def stress(start_date: str, end_date: str, ctx: Context) -> Any:
    """Daily stress for a date range (max 28 days)."""
    return fetch(ctx, f"/usersummary-service/stats/stress/daily/{start_date}/{end_date}")


@mcp.tool()
def hydration(start_date: str, end_date: str, ctx: Context) -> Any:
    """Daily hydration for a date range (max 31 days)."""
    return fetch(ctx, f"/usersummary-service/stats/hydration/daily/{start_date}/{end_date}")


@mcp.tool()
def body_battery(start_date: str, end_date: str, ctx: Context, chunks: int | None = None) -> Any:
    """Daily Body Battery reports for a date range (max 31 days). chunks splits each day into that many
    intervals and returns each interval's lowest and highest reading."""
    return fetch(
        ctx,
        "/wellness-service/wellness/bodyBattery/reports/daily",
        {"startDate": start_date, "endDate": end_date, "chunks": chunks},
    )


@mcp.tool()
def heart_rate(calendar_date: str, ctx: Context) -> Any:
    """Heart rate for one day: resting, min/max and the intraday series."""
    return fetch(ctx, "/wellness-service/wellness/dailyHeartRate", {"date": calendar_date})


@mcp.tool()
def spo2(calendar_date: str, ctx: Context) -> Any:
    """Pulse ox (SpO2) readings and acclimation for one day."""
    return fetch(ctx, f"/wellness-service/wellness/daily/spo2acclimation/{calendar_date}")


@mcp.tool()
def wellness_activity_summaries(start_date: str, end_date: str, ctx: Context) -> Any:
    """Wellness activity summaries for an inclusive date range."""
    return fetch(ctx, f"/wellnessactivity-service/activity/summary/range/{start_date}/{end_date}")


# --- Sleep and HRV ---


@mcp.tool()
def sleep(calendar_date: str, ctx: Context) -> Any:
    """Detailed sleep for one night (stages, score, timing), keyed by the date you woke up."""
    return fetch(ctx, "/sleep-service/sleep/dailySleep", {"date": calendar_date})


@mcp.tool()
def sleep_summaries(start_date: str, end_date: str, ctx: Context) -> Any:
    """Per-night sleep summaries for a date range (max 31 days)."""
    return fetch(
        ctx, "/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": start_date, "endDate": end_date}
    )


@mcp.tool()
def hrv(calendar_date: str, ctx: Context) -> Any:
    """Heart rate variability (HRV) summary and overnight readings for one date."""
    return fetch(ctx, f"/hrv-service/hrv/{calendar_date}")


@mcp.tool()
def hrv_summaries(start_date: str, end_date: str, ctx: Context) -> Any:
    """Daily HRV summaries and status for a date range."""
    return fetch(ctx, f"/hrv-service/hrv/daily/{start_date}/{end_date}")


# --- Fitness ---


@mcp.tool()
def fitness_age(calendar_date: str, ctx: Context, full_history_scan: bool = True) -> Any:
    """Latest Fitness Age as of a date. full_history_scan=false only looks at the prior 28 days."""
    return fetch(
        ctx, f"/fitnessage-service/fitnessage/v2/{calendar_date}", {"fullHistoryScan": full_history_scan}
    )


@mcp.tool()
def heart_rate_zones(ctx: Context, generate_defaults: bool = True) -> Any:
    """Heart rate zone configuration. generate_defaults returns default zones when none are configured."""
    return fetch(ctx, "/biometric-service/heartRateZones/v2", {"generateDefaults": generate_defaults})


# --- Weight and nutrition ---


@mcp.tool()
def weight_summaries(start_date: str, end_date: str, ctx: Context, include_all: bool = False) -> Any:
    """Weight summaries for a date range: daily latest weights, range average and adjacent weights.
    include_all returns every entry per day instead of only the latest."""
    return fetch(ctx, f"/weight-service/weight/range/{start_date}/{end_date}", {"includeAll": include_all})


@mcp.tool()
def weight_entries(start_date: str, end_date: str, ctx: Context) -> Any:
    """Every weight and body composition entry in a date range."""
    return fetch(ctx, f"/weight-service/weight/all/{start_date}/{end_date}")


@mcp.tool()
def weight_goals(start_date: str, end_date: str, ctx: Context) -> Any:
    """Effective weight goals and goal ranges for a date range."""
    return fetch(ctx, f"/goal-service/goal/user/effective/weightgoal/{start_date}/{end_date}")


@mcp.tool()
def food_logs(
    calendar_date: str,
    ctx: Context,
    include_goals: bool = True,
    include_nutrition: bool = True,
    include_media: bool = True,
    meal_id: int | None = None,
) -> Any:
    """One day's nutrition: goals, nutrients consumed and logged food events. meal_id narrows to one meal
    (breakfast, lunch, dinner or snacks)."""
    return fetch(
        ctx,
        f"/nutrition-service/food/logs/{calendar_date}",
        {
            "includeGoals": include_goals,
            "includeNutrition": include_nutrition,
            "includeMedia": include_media,
            "mealId": meal_id,
        },
    )


# --- Women's health ---


@mcp.tool()
def menstrual_cycle_summary(calendar_date: str, ctx: Context) -> Any:
    """Menstrual cycle summary as of a date."""
    return fetch(ctx, f"/periodichealth-service/menstrualcycle/summary/{calendar_date}")


@mcp.tool()
def menstrual_cycle_day_view(
    calendar_date: str,
    ctx: Context,
    today_date: str | None = None,
    reduced_response: bool = False,
    skip_baby_movement: bool = False,
    include_analysis: bool = False,
) -> Any:
    """Detailed menstrual cycle day: logged symptoms, flow and mood, the cycle summary, and pregnancy
    weight goal ranges. today_date anchors predictions; skip_baby_movement drops kick counts."""
    return fetch(
        ctx,
        f"/periodichealth-service/menstrualcycle/dayview/{calendar_date}",
        {
            "todayDate": today_date,
            "reducedResponse": reduced_response,
            "skipbm": skip_baby_movement,
            "includeAnalysis": include_analysis,
        },
    )


# --- Profile ---


@mcp.tool()
def profile_settings(ctx: Context) -> Any:
    """The user's Garmin profile settings (units, birth date, height, weight, sleep times...)."""
    return fetch(ctx, "/userprofile-service/userprofile/settings")


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
