import requests
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")


def geocode_address(address: str) -> dict:
    resp = requests.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={"address": address, "key": API_KEY},
        timeout=10
    )
    resp.raise_for_status()
    data = resp.json()

    if data["status"] != "OK":
        raise ValueError(f"Geocoding failed for '{address}': {data['status']}")

    location = data["results"][0]["geometry"]["location"]
    return {
        "lat": location["lat"],
        "lng": location["lng"],
        "resolved_address": data["results"][0]["formatted_address"]
    }

def search_restaurants(lat: float, lng: float, radius: int = 1500) -> list:
    url = "https://places.googleapis.com/v1/places:searchNearby"

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": "places.displayName,places.rating,places.userRatingCount,places.priceLevel,places.formattedAddress,places.id"
    }

    body = {
        "includedTypes": ["restaurant"],
        "maxResultCount": 20,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": radius
            }
        }
    }

    resp = requests.post(url, headers=headers, json=body, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    return data.get("places", [])