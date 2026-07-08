# Google Maps MCP Server

A general-purpose Google Maps MCP server exposing geocoding and place search
tools over the Model Context Protocol.

## Current tools

| Tool | Description |
|------|-------------|
| `get_geocode_address` | Address -> latitude/longitude + resolved address |
| `get_reverse_geocode` | Latitude/longitude -> human-readable address + place_id |
| `find_nearby_places` | Category-based nearby place search (ranked by rating) |
| `best_places_near` | Geocode an address and search nearby in one call |
| `get_directions_between` | Directions between two points (driving/walking/transit/bicycling): distance, duration, step summary |
| `get_distance_matrix` | Travel time/distance between multiple origins and destinations at once |
| `get_place_details` | place_id → phone, hours, website, price level, rating, review snippets |

Supported place categories: `food_and_drink`, `entertainment_and_recreation`,
`shopping`, `sports`, `automotive`, `health_and_wellness`, `lodging`.

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file next to `server.py`:

```
GOOGLE_MAPS_API_KEY=your-key-here
```

## Required Google Cloud APIs

Enable these in your Google Cloud project:

- Geocoding API
- Places API (New)
- Routes API (used for directions and distance matrix — the legacy
  Directions/Distance Matrix APIs are not available to new Google Cloud projects)
