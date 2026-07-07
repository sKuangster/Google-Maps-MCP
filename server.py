from mcp.server.fastmcp import FastMCP
from client import geocode_address, search_restaurants, rank_restaurants

mcp = FastMCP("date-planner")

@mcp.tool()
def get_geocode_address(address: str) -> dict:
    """Returns a dict of latitude, longitude, and original address given a street address."""
    try:
        return geocode_address(address)
    except Exception as e:
        return {"error": f"Could not get geocode address {e}"}

@mcp.tool()
def get_nearby_restaurants(lat: float, lng: float, radius: int = 1500, min_reviews: int = 5) -> list:
    """Find and rank the best-rated restaurants near a given address."""
    print(f"DEBUG: lat={lat!r} type={type(lat)}, lng={lng!r} type={type(lng)}")
    try:
        restaurants = search_restaurants(lat, lng, radius)
        return rank_restaurants(restaurants)
    except Exception as e:
        return [{"error": str(e)}]