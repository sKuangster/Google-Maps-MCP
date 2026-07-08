from mcp.server.fastmcp import FastMCP
from client import (
    geocode_address,
    reverse_geocode,
    search_places,
    rank_places,
    get_directions,
    compute_distance_matrix,
    fetch_place_details,
    DirectionsRequest,
    DistanceMatrixRequest,
    PlaceCategory,
    PlaceSearchRequest,
)

mcp = FastMCP("place-finder")


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
def best_places_near(address: str, category: PlaceCategory, radius: int = 1500, min_reviews: int = 5) -> list:
    """Find and rank the best-rated places near a given address, filtered by category.
    Combines geocoding and place search in one call."""
    try:
        geo = geocode_address(address)
        raw = search_places(geo.lat, geo.lng, category, radius)
        return rank_places(raw, min_reviews)
    except Exception as e:
        return [{"error": str(e)}]


if __name__ == "__main__":
    mcp.run(transport="stdio")