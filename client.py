import requests
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
RESTAURANT_TYPES = [
    "restaurant", "american_restaurant", "bar", "bar_and_grill", "bistro", "brewery", "pub",
    "fast_food_restaurant", "fine_dining_restaurant", "family_restaurant", "diner",
    "breakfast_restaurant", "brunch_restaurant", "buffet_restaurant",
    "italian_restaurant", "french_restaurant", "spanish_restaurant", "greek_restaurant",
    "german_restaurant", "british_restaurant", "mediterranean_restaurant", "european_restaurant",
    "mexican_restaurant", "latin_american_restaurant", "brazilian_restaurant", "caribbean_restaurant",
    "middle_eastern_restaurant", "lebanese_restaurant", "turkish_restaurant",
    "african_restaurant", "ethiopian_restaurant", "moroccan_restaurant",
    "indian_restaurant", "pakistani_restaurant",
    "chinese_restaurant", "japanese_restaurant", "sushi_restaurant", "ramen_restaurant",
    "korean_restaurant", "korean_barbecue_restaurant", "thai_restaurant", "vietnamese_restaurant",
    "indonesian_restaurant", "asian_restaurant", "asian_fusion_restaurant",
    "seafood_restaurant", "steak_house", "barbecue_restaurant", "pizza_restaurant",
    "hamburger_restaurant", "sandwich_shop",
    "vegetarian_restaurant", "vegan_restaurant",
    "cafe", "coffee_shop", "bakery", "dessert_shop", "ice_cream_shop",
]

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
        "includedTypes": RESTAURANT_TYPES[:50], # API only allows 50 types per request
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

def rank_restaurants(results: list, min_reviews: int = 5) -> list:
    filtered = filter(lambda x: x.get("userRatingCount", 0) >= min_reviews, results)
    return sorted(filtered, key=lambda p: (p.get("rating", 0), p.get("userRatingCount", 0)), reverse=True)