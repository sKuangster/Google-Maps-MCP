from datetime import date

from enrichment import extract_hours_snippets, hours_verdict

# Google Places day convention: 0=Sunday .. 6=Saturday.
# Fixed dates used throughout; sanity-check the weekday assumptions.
assert date(2026, 7, 13).weekday() == 0  # Monday
assert date(2026, 7, 18).weekday() == 5  # Saturday
assert date(2026, 7, 20).weekday() == 0  # Monday

MONDAY_9_TO_5 = [{"open": {"day": 1, "hour": 9, "minute": 0},
                  "close": {"day": 1, "hour": 17, "minute": 0}}]

FRIDAY_OVERNIGHT = [{"open": {"day": 5, "hour": 20, "minute": 0},
                     "close": {"day": 6, "hour": 2, "minute": 0}}]

ALWAYS_OPEN = [{"open": {"day": 0, "hour": 0, "minute": 0}}]


def test_open_within_regular_period():
    result = hours_verdict("2026-07-13T10:00", regular_periods=MONDAY_9_TO_5)
    assert result["verdict"] == "open"
    assert result["source"] == "regular_opening_hours"


def test_closed_outside_regular_period():
    result = hours_verdict("2026-07-13T18:00", regular_periods=MONDAY_9_TO_5)
    assert result["verdict"] == "closed"


def test_open_during_overnight_period_after_midnight():
    result = hours_verdict("2026-07-18T01:00", regular_periods=FRIDAY_OVERNIGHT)
    assert result["verdict"] == "open"


def test_always_open_place():
    result = hours_verdict("2026-07-13T03:00", regular_periods=ALWAYS_OPEN)
    assert result["verdict"] == "open"


def test_no_hours_data_is_unknown():
    result = hours_verdict("2026-07-13T10:00")
    assert result["verdict"] == "unknown"


def test_current_hours_override_regular_for_covered_dates():
    # Regular hours say Monday 9-17, but current (holiday-adjusted) hours have
    # no period on Monday 2026-07-13 despite covering surrounding dates.
    current = {"periods": [
        {"open": {"day": 0, "hour": 12, "minute": 0, "date": {"year": 2026, "month": 7, "day": 12}},
         "close": {"day": 0, "hour": 17, "minute": 0, "date": {"year": 2026, "month": 7, "day": 12}}},
        {"open": {"day": 2, "hour": 9, "minute": 0, "date": {"year": 2026, "month": 7, "day": 14}},
         "close": {"day": 2, "hour": 17, "minute": 0, "date": {"year": 2026, "month": 7, "day": 14}}},
    ]}
    result = hours_verdict("2026-07-13T10:00", regular_periods=MONDAY_9_TO_5,
                           current_hours=current)
    assert result["verdict"] == "closed"
    assert result["source"] == "current_opening_hours"


def test_visit_beyond_current_window_falls_back_to_regular():
    current = {"periods": [
        {"open": {"day": 0, "hour": 12, "minute": 0, "date": {"year": 2026, "month": 7, "day": 12}},
         "close": {"day": 0, "hour": 17, "minute": 0, "date": {"year": 2026, "month": 7, "day": 12}}},
    ]}
    result = hours_verdict("2026-07-20T10:00", regular_periods=MONDAY_9_TO_5,
                           current_hours=current)
    assert result["verdict"] == "open"
    assert result["source"] == "regular_opening_hours"


def test_timezone_aware_visit_converted_to_place_local_time():
    # 14:00 UTC == 10:00 in New York (UTC-4 in July) -> inside Monday 9-17
    result = hours_verdict("2026-07-13T14:00:00+00:00", regular_periods=MONDAY_9_TO_5,
                           utc_offset_minutes=-240)
    assert result["verdict"] == "open"


def test_extract_hours_snippets_finds_hour_shaped_lines():
    text = "\n".join([
        "Welcome to Roman's, a neighborhood restaurant.",
        "Hours: Mon-Fri 11am-10pm",
        "Open daily from 5 PM until midnight",
        "Closed Tuesdays",
        "Our chef trained in Lyon.",
        "Reservations recommended.",
    ])
    snippets = extract_hours_snippets(text)
    assert "Hours: Mon-Fri 11am-10pm" in snippets
    assert "Open daily from 5 PM until midnight" in snippets
    assert "Closed Tuesdays" in snippets
    assert "Our chef trained in Lyon." not in snippets
    assert "Reservations recommended." not in snippets


def test_extract_hours_snippets_dedupes_and_caps():
    text = "\n".join(["Open 9am-5pm"] * 30 + [f"Opens at {h}pm daily" for h in range(1, 12)])
    snippets = extract_hours_snippets(text, limit=10)
    assert len(snippets) == 10
    assert snippets.count("Open 9am-5pm") == 1
