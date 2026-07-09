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
| `create_embedded_map` | Ordered stops (2-22) + travel mode -> embedded interactive multi-stop route map (MCP Apps) with an open-in-Google-Maps fallback link |
| `enrich_location` | Place -> Google hours/details + parsed website extract + web search, with an open/closed verdict for a planned `visit_time` |

Supported place categories: `food_and_drink`, `entertainment_and_recreation`,
`shopping`, `sports`, `automotive`, `health_and_wellness`, `lodging`.

Directions and distance-matrix inputs accept street addresses, place names, or
`"lat,lng"` strings.

### Itinerary planning

`find_nearby_places` results include each place's coordinates, and its
docstring teaches the calling model the walking-center pattern: recenter each
follow-up search on the previously chosen stop instead of searching a whole
area from one fixed center, so multi-stop plans cluster geographically.
`enrich_location` validates that each stop is open at its planned visit time,
and `create_embedded_map` renders the finished itinerary. The embedded map is
an [MCP Apps](https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/)
view (`map_app.html`) served from `ui://google-maps/itinerary-map.html`;
hosts without MCP Apps support still get the fallback maps link. Transit
itineraries render per-leg maps because the Maps Embed API does not support
waypoints in transit mode.

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

The server refuses to start if `MCP_SHARED_SECRET` is unset.

## Authentication

Requests to `/mcp` are accepted with either credential; everything else gets
`401`:

1. **`X-MCP-Secret` header** matching `MCP_SHARED_SECRET` — for Claude Code
   and scripts.
2. **OAuth 2.1 Bearer token** — for claude.ai / Claude Desktop custom
   connectors. The server implements the MCP authorization spec itself
   (metadata discovery, dynamic client registration, PKCE authorization-code
   flow with refresh tokens): connecting from Claude opens a consent page
   where you enter the shared secret once, and Claude holds tokens from then
   on. Tokens are stateless HMAC-signed blobs keyed off `MCP_SHARED_SECRET`,
   so they survive restarts, and rotating the secret revokes all of them.
   OAuth redirect targets are restricted to Claude's callback URLs and
   loopback addresses; extend with a comma-separated `OAUTH_EXTRA_REDIRECTS`
   env var if needed.

`GET /healthz` and the OAuth endpoints (`/.well-known/*`, `/register`,
`/authorize`, `/token`) are the only unauthenticated routes.

## Required Google Cloud APIs

Enable these in your Google Cloud project:

- Geocoding API
- Places API (New)
- Routes API (used for directions and distance matrix — the legacy
  Directions/Distance Matrix APIs are not available to new Google Cloud projects)
- Time Zone API
- Maps Embed API (embedded itinerary maps — free of charge, unlimited). Embed
  URLs expose their API key client-side; optionally set `MAPS_EMBED_API_KEY`
  to a separate HTTP-referrer-restricted key.

## Cost control

The quota-sensitive endpoints are wrapped with an in-memory TTL cache plus a
minimum-interval throttle (see `cost_control.py`):

| Endpoint | Cache TTL | Throttle |
|----------|-----------|----------|
| Directions (`computeRoutes`) | 5 min | 1 call / 0.5 s |
| Distance Matrix (`computeRouteMatrix`) | 5 min | 1 call / 1 s |
| Place Details | 1 hour | 1 call / 0.5 s |
| Website fetch (`enrich_location`) | 1 hour | 1 call / 1 s |
| DuckDuckGo search (`enrich_location`) | 1 hour | 1 call / 1 s |

`enrich_location`'s web search uses keyless DuckDuckGo (`ddgs`); from
datacenter IPs it can occasionally rate-limit, in which case that leg of the
response degrades to an error field while Google data and the website extract
still come back. Name-to-place_id resolution uses Text Search with an ID-only
field mask, which is a free SKU.

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

Free-tier caveat: Render spins the instance down after ~15 minutes without
inbound traffic, and the 30–60 s cold start is longer than claude.ai's
connector timeout. To prevent this, the server pings its own `/healthz` every
10 minutes while running on Render (see `start_keep_alive` in `server.py`),
keeping the instance warm around the clock. One always-on service fits within
the free tier's 750 instance-hours/month.

Pushing to `main` auto-deploys the service.

## Connecting Claude

**claude.ai / Claude Desktop (custom connector, OAuth)**

1. Go to **Settings → Connectors → Add custom connector**.
2. Enter `https://<your-service>.onrender.com/mcp` as the URL. Leave the
   Advanced settings (OAuth client ID/secret) empty — the server supports
   dynamic client registration.
3. Click **Add**, then **Connect**. You'll be redirected to the server's
   consent page: enter your `MCP_SHARED_SECRET` and click Authorize.

Free-tier note: if the Render service has spun down, the consent page or
first connection attempt may time out while it cold-starts — retry after
~30 s.

**Claude Code** — either auth method works:

```bash
# Header auth (no browser round-trip):
claude mcp add --transport http google-maps \
  https://<your-service>.onrender.com/mcp \
  --header "X-MCP-Secret: <your-secret>"

# Or OAuth: add without the header, then run /mcp in a session and
# pick "Authenticate" — a browser opens the same consent page.
```

## Project layout

| File | Role |
|------|------|
| `client.py` | Google Maps API wrappers, Pydantic models, enums — errors propagate naturally |
| `server.py` | MCP tool definitions (try/except at the boundary), auth middleware, HTTP transport |
| `enrichment.py` | Location enrichment: website extract, web search, deterministic hours verdict |
| `map_app.html` | MCP Apps view for `create_embedded_map` (rendered by the host in a sandboxed iframe) |
| `oauth.py` | Stateless OAuth 2.1 authorization server (metadata, registration, PKCE flow) |
| `cost_control.py` | TTL cache + throttle decorator for expensive endpoints |
| `render.yaml` | Render free-tier deployment blueprint |
| `tests/` | pytest suite for the pure logic (embed URLs, hours verdicts, snippet extraction) |

Run tests with `pip install -r requirements-dev.txt` then `python -m pytest`.
