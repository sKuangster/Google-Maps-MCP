import hmac
import logging
import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from oauth import OAuthProvider
from client import (
    geocode_address,
    reverse_geocode,
    search_places,
    rank_places,
    get_directions,
    compute_distance_matrix,
    fetch_place_details,
    lookup_time_zone,
    DirectionsRequest,
    DistanceMatrixRequest,
    PlaceCategory,
    PlaceSearchRequest,
)

load_dotenv(Path(__file__).resolve().parent / ".env")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

MCP_SHARED_SECRET = os.getenv("MCP_SHARED_SECRET")

# stateless_http so requests survive restarts/redeploys without session state
mcp = FastMCP("google-maps", stateless_http=True)


# Paths that must stay reachable without credentials: platform liveness probes
# and the OAuth discovery/registration/login flow itself.
OPEN_PATHS = {"/healthz", "/register", "/token", "/authorize"}
OPEN_PREFIXES = ("/.well-known/",)


class AuthMiddleware(BaseHTTPMiddleware):
    """Require either the X-MCP-Secret header or a valid OAuth Bearer token.

    Unauthenticated requests get a 401 with a WWW-Authenticate challenge
    pointing at the protected-resource metadata, which is how MCP clients
    discover the OAuth flow.
    """

    def __init__(self, app, oauth_provider: OAuthProvider):
        super().__init__(app)
        self.oauth_provider = oauth_provider

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
            return await call_next(request)

        provided_secret = request.headers.get("X-MCP-Secret", "")
        if MCP_SHARED_SECRET and hmac.compare_digest(provided_secret, MCP_SHARED_SECRET):
            return await call_next(request)

        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer ") and \
                self.oauth_provider.verify_access_token(authorization.removeprefix("Bearer ")):
            return await call_next(request)

        logger.warning("Rejected request to %s: no valid X-MCP-Secret or Bearer token", path)
        return JSONResponse(
            {"error": "unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": (
                'Bearer resource_metadata='
                f'"{self.oauth_provider.base_url}/.well-known/oauth-protected-resource"'
            )},
        )


async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.tool()
def get_geocode_address(address: str) -> dict:
    """Returns latitude, longitude, and resolved address for a given street address."""
    try:
        return geocode_address(address).model_dump()
    except Exception as e:
        return {"error": f"Could not geocode address: {e}"}


@mcp.tool()
def get_reverse_geocode(lat: float, lng: float) -> dict:
    """Returns the human-readable address and place_id for a latitude/longitude."""
    try:
        return reverse_geocode(lat, lng).model_dump()
    except Exception as e:
        return {"error": f"Could not reverse geocode ({lat}, {lng}): {e}"}


@mcp.tool()
def find_nearby_places(request: PlaceSearchRequest) -> list:
    """Find and rank the best-rated places near a location, filtered by category
    (food_and_drink, entertainment_and_recreation, shopping, sports, automotive,
    health_and_wellness, lodging)."""
    try:
        raw = search_places(request.lat, request.lng, request.category, request.radius)
        return rank_places(raw, request.min_reviews)
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
def get_directions_between(request: DirectionsRequest) -> dict:
    """Get directions between two points (each an address, place name, or 'lat,lng')
    by driving, walking, transit, or bicycling. Returns total distance, duration,
    and a turn-by-turn step summary."""
    try:
        return get_directions(request.origin, request.destination, request.mode).model_dump()
    except Exception as e:
        return {"error": f"Could not get directions: {e}"}


@mcp.tool()
def get_distance_matrix(request: DistanceMatrixRequest) -> list:
    """Get travel time and distance between multiple origins and destinations at
    once (max 10 of each). Each entry pairs one origin with one destination."""
    try:
        entries = compute_distance_matrix(request.origins, request.destinations, request.mode)
        return [e.model_dump() for e in entries]
    except Exception as e:
        return [{"error": f"Could not compute distance matrix: {e}"}]


@mcp.tool()
def get_place_details(place_id: str) -> dict:
    """Get details for a place by its place_id: phone number, opening hours,
    website, price level, rating, and review snippets."""
    try:
        return fetch_place_details(place_id).model_dump()
    except Exception as e:
        return {"error": f"Could not fetch place details for '{place_id}': {e}"}


@mcp.tool()
def get_time_zone(lat: float, lng: float) -> dict:
    """Get the local time zone for a latitude/longitude: time zone ID and name,
    UTC offset, and the current local time."""
    try:
        return lookup_time_zone(lat, lng).model_dump()
    except Exception as e:
        return {"error": f"Could not look up time zone for ({lat}, {lng}): {e}"}


@mcp.tool()
def best_places_near(address: str, category: PlaceCategory, radius: int = 1500, min_reviews: int = 5) -> list:
    """Find and rank the best-rated places near a given address, filtered by category.
    Combines geocoding and place search in one call."""
    try:
        geo = geocode_address(address)
        raw = search_places(geo.lat, geo.lng, category, radius)
        return rank_places(raw, min_reviews)
    except Exception as e:
        return [{"error": str(e)}]


def build_app(port: int = 8000):
    if not MCP_SHARED_SECRET:
        raise RuntimeError("MCP_SHARED_SECRET is not set; refusing to start unauthenticated")

    # Render injects RENDER_EXTERNAL_URL; PUBLIC_BASE_URL overrides for other hosts
    base_url = (os.getenv("PUBLIC_BASE_URL")
                or os.getenv("RENDER_EXTERNAL_URL")
                or f"http://localhost:{port}")
    extra_redirects = {u.strip() for u in os.getenv("OAUTH_EXTRA_REDIRECTS", "").split(",")
                       if u.strip()}
    oauth_provider = OAuthProvider(base_url, MCP_SHARED_SECRET, extra_redirects)

    app = mcp.streamable_http_app()
    from starlette.routing import Route
    app.router.routes.append(Route("/healthz", healthz, methods=["GET"]))
    app.router.routes.extend(oauth_provider.routes())
    app.add_middleware(AuthMiddleware, oauth_provider=oauth_provider)
    # CORS outermost so browser preflights don't hit auth
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"], expose_headers=["Mcp-Session-Id"])
    logger.info("OAuth issuer/base URL: %s", base_url)
    return app


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    logger.info("Starting google-maps MCP server on port %d (endpoint /mcp)", port)
    uvicorn.run(build_app(port), host="0.0.0.0", port=port)