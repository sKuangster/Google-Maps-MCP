# Google Maps MCP Server

A general-purpose Google Maps MCP server: geocoding, place search, place
details, directions, distance matrices, and time zone lookup over the Model
Context Protocol, served over authenticated streamable HTTP.

## Tools

| Tool | Description |
|------|-------------|
| `get_geocode_address` | Address -> latitude/longitude + resolved address |
| `get_reverse_geocode` | Latitude/longitude -> human-readable address + place_id |
| `find_nearby_places` | Category-based nearby place search (ranked by rating) |
| `best_places_near` | Geocode an address and search nearby in one call |
| `get_directions_between` | Directions between two points (driving/walking/transit/bicycling): distance, duration, step summary |
| `get_distance_matrix` | Travel time/distance between multiple origins and destinations at once |
| `get_place_details` | place_id -> phone, hours, website, price level, rating, review snippets |
| `get_time_zone` | Latitude/longitude -> time zone ID/name, UTC offset, current local time |

Supported place categories: `food_and_drink`, `entertainment_and_recreation`,
`shopping`, `sports`, `automotive`, `health_and_wellness`, `lodging`.

Directions and distance-matrix inputs accept street addresses, place names, or
`"lat,lng"` strings.

## Setup

```bash
pip install -r requirements.txt
copy .env.example .env   # then fill in real values
python server.py         # serves http://localhost:8000/mcp
```

`.env` lives next to `server.py` and needs:

| Variable | Purpose |
|----------|---------|
| `GOOGLE_MAPS_API_KEY` | Google Maps Platform API key |
| `MCP_SHARED_SECRET` | Secret clients must send in the `X-MCP-Secret` header. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"` |

The server refuses to start if `MCP_SHARED_SECRET` is unset. Every request
except `GET /healthz` is rejected with `401` unless it carries the correct
`X-MCP-Secret` header.

## Required Google Cloud APIs

Enable these in your Google Cloud project:

- Geocoding API
- Places API (New)
- Routes API (used for directions and distance matrix — the legacy
  Directions/Distance Matrix APIs are not available to new Google Cloud projects)
- Time Zone API

## Cost control

The quota-sensitive endpoints are wrapped with an in-memory TTL cache plus a
minimum-interval throttle (see `cost_control.py`):

| Endpoint | Cache TTL | Throttle |
|----------|-----------|----------|
| Directions (`computeRoutes`) | 5 min | 1 call / 0.5 s |
| Distance Matrix (`computeRouteMatrix`) | 5 min | 1 call / 1 s |
| Place Details | 1 hour | 1 call / 0.5 s |

Repeated identical calls are served from cache and never hit Google. Cheap
endpoints (Geocoding, Time Zone) call the API directly. Note that every
distance-matrix *element* (origins × destinations) is billed as a request, so
the tool caps inputs at 10 origins × 10 destinations. The cache is per-process:
it resets on restart, which on Render's free tier includes spin-down after
idle periods.

## Deploying to Render (free tier)

The repo ships a [Render blueprint](render.yaml) (free-tier Python web service).

1. Push this repo to GitHub (already done if you're reading this there).
2. In the [Render dashboard](https://dashboard.render.com), click
   **New → Blueprint**, connect the GitHub repo, and accept the defaults from
   `render.yaml`.
3. When prompted for environment variables, set `GOOGLE_MAPS_API_KEY` and
   `MCP_SHARED_SECRET` (generate a fresh secret for production; don't reuse
   your local one).
4. Deploy. Your MCP endpoint is
   `https://<your-service>.onrender.com/mcp`, and
   `https://<your-service>.onrender.com/healthz` should return
   `{"status": "ok"}` without auth.

Free-tier caveat: the instance spins down after ~15 minutes of inactivity, so
the first request after idle takes ~30–60 s while it cold-starts.

Pushing to `main` auto-deploys the service.

## Connecting Claude

**Claude Code** (supports custom headers directly):

```bash
claude mcp add --transport http google-maps \
  https://<your-service>.onrender.com/mcp \
  --header "X-MCP-Secret: <your-secret>"
```

**Claude Desktop** — the connector UI doesn't support custom headers, so
bridge through [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) in
`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "google-maps": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://<your-service>.onrender.com/mcp",
        "--header",
        "X-MCP-Secret: <your-secret>"
      ]
    }
  }
}
```

**claude.ai (web) custom connectors** currently support only OAuth or
unauthenticated servers — there is no field for a custom secret header, so
this server's header auth won't work there without adding an OAuth layer.

## Project layout

| File | Role |
|------|------|
| `client.py` | Google Maps API wrappers, Pydantic models, enums — errors propagate naturally |
| `server.py` | MCP tool definitions (try/except at the boundary), auth middleware, HTTP transport |
| `cost_control.py` | TTL cache + throttle decorator for expensive endpoints |
| `render.yaml` | Render free-tier deployment blueprint |
