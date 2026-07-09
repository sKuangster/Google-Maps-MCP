# Design: Embedded itinerary map, location enrichment, walking-center search

**Date:** 2026-07-09
**Status:** Approved (design sections reviewed and approved individually)

Three additions to the google-maps MCP server:

1. `create_embedded_map` — new tool rendering a multi-stop itinerary route as an
   embedded interactive map via MCP Apps.
2. `enrich_location` — new tool combining Google place details, the location's
   own website, and a general web search, with opening hours as the top-priority
   output and an open/closed verdict for a planned visit time.
3. Walking-center search pattern — docstring protocol plus a field-mask fix so
   multi-stop planning recenters each search on the last chosen stop.

## Context

The server (FastMCP over authenticated streamable HTTP on Render) is layered:
`client.py` holds Google API wrappers + Pydantic models (errors propagate),
`server.py` holds thin `@mcp.tool` boundaries (try/except), `cost_control.py`
provides `@cached_and_throttled`. New work follows this layering.

The deployed server is attached to claude.ai as a custom connector, so after
deploy the new tools can be verified live from a Claude session.

## 1. `create_embedded_map`

### Mechanism: MCP Apps

MCP Apps (official MCP UI extension, Jan 2026; supported by claude.ai and
Claude Desktop) is how tool results render as interactive UI:

- Server registers an HTML view as a resource at
  `ui://google-maps/itinerary-map.html` with mimeType
  `text/html;profile=mcp-app`, declaring external domains it needs in CSP
  metadata (`meta={"ui": {"csp": {...}}}`).
- The tool declares `meta={"ui": {"resourceUri": <view uri>}}` (plus the
  legacy `"ui/resourceUri"` key, matching the official Python example).
- Host renders the view in a sandboxed iframe and delivers the tool's
  structured result via JSON-RPC over postMessage. The view HTML loads the
  official `@modelcontextprotocol/ext-apps` browser SDK from unpkg (allowlisted
  in CSP) to receive the result.

### The view

The tool result carries a Google **Maps Embed API** directions URL:

```
https://www.google.com/maps/embed/v1/directions
  ?key=<embed key>&origin=<stop 1>&destination=<stop N>
  &waypoints=<stop 2>|...|<stop N-1>&mode=<mode>
```

The view sets it as an inner iframe — one interactive map with the full
multi-stop route — plus a numbered stop list with per-stop notes.

- **Transit:** the Embed API rejects waypoints in transit mode, so the view
  renders per-leg embeds (1→2, 2→3, …) as tabs in the same view.
  Driving/walking/bicycling get the single full-route map.
- **CSP fallback (plan B):** whether google.com is allowlistable for *frame*
  embedding (vs. resource loading, which the official example proves) is
  verified empirically during implementation. If frames are blocked, the view
  loads the Maps JavaScript API as a script and draws the route itself — same
  visual, tool schema unchanged, only the HTML template differs.
- **Rendering fallback:** the tool result always includes a plain
  `https://www.google.com/maps/dir/...` share link, so if the app fails to
  render (known claude.ai MCP Apps bugs), Claude still surfaces a clickable
  multi-stop route link.

### Schema

```python
class ItineraryStop(BaseModel):
    name: str
    address: str | None = None   # address OR lat/lng required (model validator)
    lat: float | None = None
    lng: float | None = None
    notes: str | None = None     # e.g. "dinner, 7pm reservation"

class EmbeddedMapRequest(BaseModel):
    stops: list[ItineraryStop] = Field(min_length=2, max_length=22)  # 20 waypoints + ends
    mode: TravelMode = TravelMode.WALKING   # reuses existing enum
```

Result dict: `embed_url` (or per-leg `leg_embed_urls` for transit),
`maps_link`, echoed `stops` and `mode`. URL building is pure string work in
`client.py`; no Google request happens server-side, so no cache/throttle.

URL parameter per stop: `lat,lng` when coordinates are present, else the
address. `name` is display-only (stop list in the view), never sent to the
Embed API — bare names are too ambiguous to route on.

**Standalone:** takes literal stops, calls no other tool. Docstring states
stops must be in visit order and may come from any source.

### Key handling

The embed URL exposes its key client-side (normal for the Embed API).
Optional `MAPS_EMBED_API_KEY` env var supports a separate HTTP-referrer-
restricted key; falls back to `GOOGLE_MAPS_API_KEY`.

## 2. `enrich_location`

New module `enrichment.py` (web fetch/search/verdict logic), thin tool in
`server.py`. `get_place_details` stays as-is for cheap lookups.

### Input

- `place_id` **or** `name` (+ optional `address`) — resolved to a place_id via
  Places Text Search with an ID-only field mask ($0 SKU).
- `visit_time` (optional, ISO 8601, interpreted as **local time at the place**
  — stated in the docstring).
- `focus` (optional free text): whatever the calling model wants emphasized
  (price range, menu, vibe…). Steers the web-search query; the tool never
  filters or interprets by it — no hardcoded field list beyond hours.
- `max_search_results` (default 5).

### Three independent legs, each failing softly

Each leg returns its own `error` field on failure; one dead leg never breaks
the tool.

1. **Google place details** — existing cached `fetch_place_details` with the
   field mask extended: `regularOpeningHours.periods`, `currentOpeningHours`,
   `utcOffsetMinutes`. Additive optional fields on `PlaceDetails`; no billing
   SKU change (mask already pulls Enterprise-tier fields).
2. **The location's website** — from `websiteUri`: GET (10 s timeout, 2 MB
   cap, browser-ish UA), BeautifulSoup parse →
   `{url, title, meta_description, text_excerpt (~5k chars), hours_snippets}`.
   `hours_snippets` = hour-shaped lines found in page text ("Mon–Fri",
   "5pm–11pm", "closed Tuesdays") surfaced as cross-check evidence — not
   parsed into structure (formats too chaotic; the model reconciles).
3. **General web search** — `ddgs` text search on `"{name} {address} {focus}"`
   → raw `[{title, url, snippet}]`. Keyless DuckDuckGo accepted trade-off:
   occasional rate-limit/captcha from datacenter IPs degrades to an error in
   this leg only.

### Hours-first output

Response dict ordered so hours lead:

```
hours:                weekday_descriptions, periods, current (7-day incl. holidays), utc_offset_minutes
open_at_visit_time:   {verdict: open|closed|unknown, visit_time, source, detail,
                       website_evidence: [...]}   # only when visit_time given
place:                PlaceDetails dump
website:              leg 2 result or {error}
web_search:           leg 3 results or {error}
focus:                echo
```

Verdict computed deterministically from structured `periods`
(`currentOpeningHours` preferred when the visit is within 7 days); `unknown`
is a real answer directing the model to the website evidence. This pure
function gets unit tests (pytest — first tests in the repo), along with the
embed-URL builder. No network-dependent tests.

### Cost control

Website fetch and DDG search wrapped with `@cached_and_throttled`
(TTL 1 h, min interval 1 s — throttle doubles as DDG rate-limit protection).

## 3. Walking-center search pattern

`find_nearby_places(lat, lng, category, radius)` already *is* the
"search relative to a center" helper; no new tool.

1. **Field-mask fix:** add `places.location` to the searchNearby mask so every
   result carries `location: {latitude, longitude}` — without it the model
   cannot recenter on a chosen stop without an extra geocode call. Additive
   (results pass through raw); Pro-tier field, same SKU already billed.
2. **Docstring protocol** on `find_nearby_places` and `best_places_near`:
   when building a multi-stop itinerary, geocode the general area for the
   *first* search only; then use the chosen stop's `location` as the center
   for the next search (radius 800–1500 m for walking) so stops cluster
   geographically instead of jumping around.
3. `create_embedded_map`'s docstring closes the loop (stops in visit order,
   any source).

Default radius stays 1500 m; guidance lives in descriptions, so existing
single-shot behavior is unchanged. Escalation path if models ignore the
docstring: a dedicated wrapper tool — not built speculatively.

## Cost / deployment

| Item | Impact |
|------|--------|
| Maps Embed API | Enable in Cloud console; $0, unlimited |
| Maps JavaScript API | Only if CSP plan B needed; ~10k free loads/month |
| Text Search (ID-only) | $0 SKU |
| Extended details mask | No SKU change |
| DDG / website fetches | Free; outbound HTTP fine on Render free tier |
| Deps | + `beautifulsoup4`, `ddgs`; **bump `mcp`** (`meta=` kwarg on tool/resource decorators postdates 1.10; pin verified minimum) |
| Env vars | Optional `MAPS_EMBED_API_KEY` only; blueprint unchanged |

After deploy, re-sync the claude.ai connector to pick up new tools and
descriptions; then verify `create_embedded_map` end-to-end from a Claude
session against the live connector.

## Error handling summary

- Tools keep the existing boundary pattern: try/except in `server.py`
  returning `{"error": ...}`; `client.py`/`enrichment.py` raise naturally.
- `enrich_location` legs fail independently (per-leg `error` fields).
- `create_embedded_map` validates each stop has address or lat/lng
  (Pydantic model validator → clear 4xx-style tool error, not a broken map).

## Testing

- pytest (new dev dependency) for pure logic only: hours verdict
  (`open_at_visit_time`) and embed-URL builder (incl. transit leg splitting,
  waypoint encoding, 22-stop cap).
- Manual/live verification: local run + MCP inspector, then the attached
  claude.ai connector after deploy (embedded map render, enrichment output,
  recentering behavior in a real planning conversation).
