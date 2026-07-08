from mcp.server.fastmcp import FastMCP
from client import (
    geocode_address,
    reverse_geocode,
    search_places,
    rank_places,
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