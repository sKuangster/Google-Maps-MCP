import logging
import os
from enum import Enum
from pathlib import Path

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parent / ".env")
API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

logger = logging.getLogger(__name__)


class PlaceCategory(str, Enum):
    ENTERTAINMENT_AND_RECREATION = "entertainment_and_recreation"
    FOOD_AND_DRINK = "food_and_drink"
    # Add more as you build out their type lists:
    # AUTOMOTIVE = "automotive"
    # BUSINESS = "business"
    # CULTURE = "culture"
    # SHOPPING = "shopping"
    # SPORTS = "sports"


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

ACTIVITY_TYPES = [
    "amusement_park", "amusement_center", "aquarium", "zoo", "wildlife_park", "wildlife_refuge",
    "water_park", "national_park", "state_park", "city_park", "park", "botanical_garden",
    "garden", "hiking_area", "picnic_ground", "dog_park", "cycling_park",
    "bowling_alley", "casino", "movie_theater", "night_club", "karaoke", "video_arcade",
    "go_karting_venue", "miniature_golf_course", "paintball_center", "skateboard_park",
    "roller_coaster", "ferris_wheel", "indoor_playground", "off_roading_area",
    "adventure_sports_center", "marina", "vineyard",
    "concert_hall", "live_music_venue", "philharmonic_hall", "opera_house", "amphitheatre",
    "comedy_club", "dance_hall", "planetarium", "observation_deck",
    "historical_landmark", "tourist_attraction", "cultural_center", "visitor_center",
    "community_center", "convention_center", "event_venue", "banquet_hall", "wedding_venue",
]

CATEGORY_TYPES = {
    PlaceCategory.FOOD_AND_DRINK: RESTAURANT_TYPES,
    PlaceCategory.ENTERTAINMENT_AND_RECREATION: ACTIVITY_TYPES,
}


class PlaceSearchRequest(BaseModel):
    lat: float
    lng: float
    category: PlaceCategory
    radius: int = Field(default=1500, ge=100, le=50000)
    min_reviews: int = Field(default=5, ge=0)


class GeocodeResult(BaseModel):
    lat: float
    lng: float
    resolved_address: str


def geocode_address(address: str) -> GeocodeResult:
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
    return GeocodeResult(
        lat=location["lat"],
        lng=location["lng"],
        resolved_address=data["results"][0]["formatted_address"]
    )


def search_places(lat: float, lng: float, category: PlaceCategory, radius: int = 1500) -> list:
    included_types = CATEGORY_TYPES.get(category)
    if not included_types:
        raise ValueError(f"No type mapping defined for category: {category}")

    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": "places.displayName,places.rating,places.userRatingCount,places.priceLevel,places.formattedAddress,places.id"
    }
    body = {
        "includedTypes": included_types[:50],
        "maxResultCount": 20,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": radius
            }
        }
    }

    resp = requests.post(url, headers=headers, json=body, timeout=10)
    if not resp.ok:
        logger.error("Places searchNearby failed: status=%s body=%s", resp.status_code, resp.text)
    resp.raise_for_status()

    return resp.json().get("places", [])


def rank_places(results: list, min_reviews: int = 5) -> list:
    filtered = filter(lambda x: x.get("userRatingCount", 0) >= min_reviews, results)
    return sorted(
        filtered,
        key=lambda p: (p.get("rating", 0), p.get("userRatingCount", 0)),
        reverse=True
    )
