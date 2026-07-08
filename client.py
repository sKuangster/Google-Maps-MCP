import logging
import os
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from cost_control import cached_and_throttled

load_dotenv(Path(__file__).resolve().parent / ".env")
API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

logger = logging.getLogger(__name__)


class TravelMode(str, Enum):
    DRIVING = "driving"
    WALKING = "walking"
    BICYCLING = "bicycling"
    TRANSIT = "transit"


# Routes API uses different mode names than the values we expose
ROUTES_TRAVEL_MODES = {
    TravelMode.DRIVING: "DRIVE",
    TravelMode.WALKING: "WALK",
    TravelMode.BICYCLING: "BICYCLE",
    TravelMode.TRANSIT: "TRANSIT",
}


class PlaceCategory(str, Enum):
    ENTERTAINMENT_AND_RECREATION = "entertainment_and_recreation"
    FOOD_AND_DRINK = "food_and_drink"
    SHOPPING = "shopping"
    SPORTS = "sports"
    AUTOMOTIVE = "automotive"
    HEALTH_AND_WELLNESS = "health_and_wellness"
    LODGING = "lodging"


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

SHOPPING_TYPES = [
    "shopping_mall", "department_store", "market", "supermarket", "grocery_store",
    "asian_grocery_store", "food_store", "convenience_store", "warehouse_store",
    "discount_store", "wholesaler", "butcher_shop", "liquor_store",
    "clothing_store", "shoe_store", "jewelry_store", "gift_shop",
    "book_store", "cell_phone_store", "electronics_store", "hardware_store",
    "home_goods_store", "home_improvement_store", "furniture_store",
    "bicycle_store", "sporting_goods_store", "pet_store", "auto_parts_store", "store",
]

SPORTS_TYPES = [
    "arena", "stadium", "sports_complex", "sports_club", "sports_activity_location",
    "sports_coaching", "athletic_field", "fitness_center", "gym", "golf_course",
    "ice_skating_rink", "ski_resort", "swimming_pool", "playground",
    "fishing_charter", "fishing_pond",
]

AUTOMOTIVE_TYPES = [
    "car_dealer", "car_rental", "car_repair", "car_wash",
    "electric_vehicle_charging_station", "gas_station", "parking", "rest_stop",
]

HEALTH_AND_WELLNESS_TYPES = [
    "hospital", "doctor", "dental_clinic", "dentist", "chiropractor",
    "physiotherapist", "medical_lab", "pharmacy", "drugstore",
    "spa", "massage", "sauna", "skin_care_clinic", "tanning_studio",
    "wellness_center", "yoga_studio",
]

LODGING_TYPES = [
    "hotel", "motel", "resort_hotel", "extended_stay_hotel", "bed_and_breakfast",
    "inn", "japanese_inn", "budget_japanese_inn", "guest_house", "private_guest_room",
    "hostel", "cottage", "farmstay", "campground", "camping_cabin", "rv_park",
    "mobile_home_park", "lodging",
]

CATEGORY_TYPES = {
    PlaceCategory.FOOD_AND_DRINK: RESTAURANT_TYPES,
    PlaceCategory.ENTERTAINMENT_AND_RECREATION: ACTIVITY_TYPES,
    PlaceCategory.SHOPPING: SHOPPING_TYPES,
    PlaceCategory.SPORTS: SPORTS_TYPES,
    PlaceCategory.AUTOMOTIVE: AUTOMOTIVE_TYPES,
    PlaceCategory.HEALTH_AND_WELLNESS: HEALTH_AND_WELLNESS_TYPES,
    PlaceCategory.LODGING: LODGING_TYPES,
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


class ReverseGeocodeResult(BaseModel):
    formatted_address: str
    place_id: str


class DirectionsRequest(BaseModel):
    origin: str = Field(description="Street address, place name, or 'lat,lng'")
    destination: str = Field(description="Street address, place name, or 'lat,lng'")
    mode: TravelMode = TravelMode.DRIVING


class DirectionsStep(BaseModel):
    instruction: str
    distance: str | None = None
    duration: str | None = None


class DirectionsResult(BaseModel):
    distance_meters: int
    distance_text: str
    duration_seconds: int
    duration_text: str
    steps: list[DirectionsStep]


class DistanceMatrixRequest(BaseModel):
    origins: list[str] = Field(min_length=1, max_length=10,
                               description="Addresses, place names, or 'lat,lng' pairs")
    destinations: list[str] = Field(min_length=1, max_length=10,
                                    description="Addresses, place names, or 'lat,lng' pairs")
    mode: TravelMode = TravelMode.DRIVING


class DistanceMatrixEntry(BaseModel):
    origin_index: int
    destination_index: int
    origin: str
    destination: str
    distance_meters: int | None = None
    duration_seconds: int | None = None
    condition: str


class PlaceReview(BaseModel):
    rating: float | None = None
    text: str | None = None
    author: str | None = None
    published: str | None = None


class TimeZoneResult(BaseModel):
    time_zone_id: str
    time_zone_name: str
    utc_offset_seconds: int
    local_time: str


class PlaceDetails(BaseModel):
    place_id: str
    name: str
    formatted_address: str | None = None
    phone_number: str | None = None
    website: str | None = None
    price_level: str | None = None
    rating: float | None = None
    user_rating_count: int | None = None
    opening_hours: list[str] = []
    reviews: list[PlaceReview] = []


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


def reverse_geocode(lat: float, lng: float) -> ReverseGeocodeResult:
    resp = requests.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={"latlng": f"{lat},{lng}", "key": API_KEY},
        timeout=10
    )
    resp.raise_for_status()
    data = resp.json()

    if data["status"] != "OK":
        raise ValueError(f"Reverse geocoding failed for ({lat}, {lng}): {data['status']}")

    top = data["results"][0]
    return ReverseGeocodeResult(
        formatted_address=top["formatted_address"],
        place_id=top["place_id"]
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


def _waypoint(location: str) -> dict:
    """Build a Routes API waypoint from either a 'lat,lng' pair or a free-form address."""
    parts = location.split(",")
    if len(parts) == 2:
        try:
            lat, lng = float(parts[0]), float(parts[1])
            return {"location": {"latLng": {"latitude": lat, "longitude": lng}}}
        except ValueError:
            pass
    return {"address": location}


def _duration_seconds(duration: str) -> int:
    # Routes API durations are protobuf strings like "1234s"
    return int(float(duration.rstrip("s") or 0))


@cached_and_throttled(ttl_seconds=300, min_interval_seconds=0.5)
def get_directions(origin: str, destination: str, mode: TravelMode = TravelMode.DRIVING) -> DirectionsResult:
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": (
            "routes.distanceMeters,routes.duration,routes.localizedValues,"
            "routes.legs.steps.navigationInstruction,routes.legs.steps.localizedValues"
        ),
    }
    body = {
        "origin": _waypoint(origin),
        "destination": _waypoint(destination),
        "travelMode": ROUTES_TRAVEL_MODES[mode],
    }
    if mode == TravelMode.DRIVING:
        body["routingPreference"] = "TRAFFIC_AWARE"

    resp = requests.post(url, headers=headers, json=body, timeout=15)
    if not resp.ok:
        logger.error("Routes computeRoutes failed: status=%s body=%s", resp.status_code, resp.text)
    resp.raise_for_status()

    routes = resp.json().get("routes", [])
    if not routes:
        raise ValueError(f"No {mode.value} route found from '{origin}' to '{destination}'")

    route = routes[0]
    steps = []
    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            instruction = step.get("navigationInstruction", {}).get("instructions")
            if not instruction:
                continue
            localized = step.get("localizedValues", {})
            steps.append(DirectionsStep(
                instruction=instruction,
                distance=localized.get("distance", {}).get("text"),
                duration=localized.get("staticDuration", {}).get("text"),
            ))

    localized = route.get("localizedValues", {})
    return DirectionsResult(
        distance_meters=route.get("distanceMeters", 0),
        distance_text=localized.get("distance", {}).get("text", ""),
        duration_seconds=_duration_seconds(route.get("duration", "0s")),
        duration_text=localized.get("duration", {}).get("text", ""),
        steps=steps,
    )


@cached_and_throttled(ttl_seconds=300, min_interval_seconds=1.0)
def compute_distance_matrix(origins: list[str], destinations: list[str],
                            mode: TravelMode = TravelMode.DRIVING) -> list[DistanceMatrixEntry]:
    url = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": "originIndex,destinationIndex,distanceMeters,duration,condition,status",
    }
    body = {
        "origins": [{"waypoint": _waypoint(o)} for o in origins],
        "destinations": [{"waypoint": _waypoint(d)} for d in destinations],
        "travelMode": ROUTES_TRAVEL_MODES[mode],
    }
    if mode == TravelMode.DRIVING:
        body["routingPreference"] = "TRAFFIC_AWARE"

    resp = requests.post(url, headers=headers, json=body, timeout=20)
    if not resp.ok:
        logger.error("Routes computeRouteMatrix failed: status=%s body=%s",
                     resp.status_code, resp.text)
    resp.raise_for_status()

    entries = []
    for element in resp.json():
        origin_index = element.get("originIndex", 0)
        destination_index = element.get("destinationIndex", 0)
        route_exists = element.get("condition") == "ROUTE_EXISTS"
        entries.append(DistanceMatrixEntry(
            origin_index=origin_index,
            destination_index=destination_index,
            origin=origins[origin_index],
            destination=destinations[destination_index],
            distance_meters=element.get("distanceMeters") if route_exists else None,
            duration_seconds=_duration_seconds(element["duration"])
                             if route_exists and "duration" in element else None,
            condition="OK" if route_exists else element.get("condition", "UNKNOWN"),
        ))
    entries.sort(key=lambda e: (e.origin_index, e.destination_index))
    return entries


def lookup_time_zone(lat: float, lng: float) -> TimeZoneResult:
    now = int(time.time())
    resp = requests.get(
        "https://maps.googleapis.com/maps/api/timezone/json",
        params={"location": f"{lat},{lng}", "timestamp": now, "key": API_KEY},
        timeout=10
    )
    resp.raise_for_status()
    data = resp.json()

    if data["status"] != "OK":
        raise ValueError(f"Time zone lookup failed for ({lat}, {lng}): {data['status']}")

    utc_offset = data["rawOffset"] + data["dstOffset"]
    local_time = datetime.fromtimestamp(now, tz=timezone(timedelta(seconds=utc_offset)))
    return TimeZoneResult(
        time_zone_id=data["timeZoneId"],
        time_zone_name=data["timeZoneName"],
        utc_offset_seconds=utc_offset,
        local_time=local_time.isoformat(),
    )


@cached_and_throttled(ttl_seconds=3600, min_interval_seconds=0.5)
def fetch_place_details(place_id: str) -> PlaceDetails:
    url = f"https://places.googleapis.com/v1/places/{place_id}"
    headers = {
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": (
            "id,displayName,formattedAddress,internationalPhoneNumber,websiteUri,"
            "priceLevel,rating,userRatingCount,regularOpeningHours.weekdayDescriptions,"
            "reviews.rating,reviews.text.text,reviews.authorAttribution.displayName,"
            "reviews.relativePublishTimeDescription"
        ),
    }

    resp = requests.get(url, headers=headers, timeout=10)
    if not resp.ok:
        logger.error("Place details failed for %s: status=%s body=%s",
                     place_id, resp.status_code, resp.text)
    resp.raise_for_status()
    data = resp.json()

    price_level = data.get("priceLevel")
    if price_level:
        price_level = price_level.removeprefix("PRICE_LEVEL_").lower()

    reviews = [
        PlaceReview(
            rating=review.get("rating"),
            text=review.get("text", {}).get("text"),
            author=review.get("authorAttribution", {}).get("displayName"),
            published=review.get("relativePublishTimeDescription"),
        )
        for review in data.get("reviews", [])
    ]

    return PlaceDetails(
        place_id=data.get("id", place_id),
        name=data.get("displayName", {}).get("text", ""),
        formatted_address=data.get("formattedAddress"),
        phone_number=data.get("internationalPhoneNumber"),
        website=data.get("websiteUri"),
        price_level=price_level,
        rating=data.get("rating"),
        user_rating_count=data.get("userRatingCount"),
        opening_hours=data.get("regularOpeningHours", {}).get("weekdayDescriptions", []),
        reviews=reviews,
    )


def rank_places(results: list, min_reviews: int = 5) -> list:
    filtered = filter(lambda x: x.get("userRatingCount", 0) >= min_reviews, results)
    return sorted(
        filtered,
        key=lambda p: (p.get("rating", 0), p.get("userRatingCount", 0)),
        reverse=True
    )
