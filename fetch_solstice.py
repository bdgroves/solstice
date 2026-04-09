#!/usr/bin/env python3
"""
SOLSTICE — fetch_solstice.py

Compute current astronomical data for Chaco Canyon and write static JSON
for the dashboard. Run automatically via GitHub Actions twice daily.

Usage:
    pixi run fetch
    python fetch_solstice.py
"""

import ephem
import json
import math
from datetime import datetime, timezone
from pathlib import Path

# ── Chaco Canyon observer coordinates ─────────────────────────────────────────

OBSERVER = {
    "lat":       "36.0608",
    "lon":       "-107.9631",
    "elevation": 1920,       # meters above sea level
    "name":      "Chaco Culture National Historical Park",
    "tz_offset": -6,         # Mountain Standard Time (UTC−6; MDT would be −5)
}

# Casa Rinconada's known summer-solstice alignment azimuth (Sofaer et al. 1979)
RINCONADA_ALIGNMENT_AZ = 66.5   # degrees
ALIGNMENT_TOLERANCE    = 3.5    # degrees — within this we call it "active"


# ── Helpers ────────────────────────────────────────────────────────────────────

def compass(deg: float) -> str:
    """Convert decimal azimuth to 16-point compass label."""
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    return dirs[round(deg / 22.5) % 16]


def moon_phase_name(pct: float) -> str:
    """Return a descriptive lunar phase name from illumination percentage."""
    thresholds = [
        (3,    "New Moon"),
        (20,   "Waxing Crescent"),
        (30,   "First Quarter"),
        (47,   "Waxing Gibbous"),
        (53,   "Full Moon"),
        (70,   "Waning Gibbous"),
        (80,   "Last Quarter"),
        (97,   "Waning Crescent"),
        (100,  "New Moon"),
    ]
    for threshold, name in thresholds:
        if pct <= threshold:
            return name
    return "New Moon"


def fmt_event(ephem_date: "ephem.Date", now_naive: "datetime") -> dict:
    """Format an ephem date as a display-friendly dict."""
    dt = ephem_date.datetime()
    days_away = max(0, (dt - now_naive).days)
    # Cross-platform day without leading zero
    day = dt.day
    display = f"{dt.strftime('%B')} {day}, {dt.year}"
    return {
        "iso":       dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "display":   display,
        "days_away": days_away,
    }


def build_observer(date=None) -> "ephem.Observer":
    obs = ephem.Observer()
    obs.lat       = OBSERVER["lat"]
    obs.lon       = OBSERVER["lon"]
    obs.elevation = OBSERVER["elevation"]
    obs.horizon   = "-0:34"  # standard atmospheric refraction
    if date is not None:
        obs.date = date
    return obs


# ── Main computation ──────────────────────────────────────────────────────────

def compute() -> dict:
    now_utc   = datetime.now(timezone.utc)
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)   # ephem needs naive UTC
    obs = build_observer(now_utc)

    sun  = ephem.Sun(obs)
    moon = ephem.Moon(obs)

    sun_az  = math.degrees(sun.az)
    sun_alt = math.degrees(sun.alt)
    moon_az  = math.degrees(moon.az)
    moon_alt = math.degrees(moon.alt)

    # ── Today's sunrise / sunset azimuths ─────────────────────────────────────
    sunrise_az = sunset_az = None
    try:
        obs_rise = build_observer(now_utc)
        prev_rise = obs_rise.previous_rising(ephem.Sun())
        obs_rise.date = prev_rise
        sunrise_az = math.degrees(ephem.Sun(obs_rise).az)

        obs_set = build_observer(now_utc)
        next_set = obs_set.next_setting(ephem.Sun())
        obs_set.date = next_set
        sunset_az = math.degrees(ephem.Sun(obs_set).az)
    except Exception as exc:
        print(f"  ⚠  Could not compute horizon events: {exc}")

    # ── Casa Rinconada alignment check ────────────────────────────────────────
    alignment_active = False
    alignment_delta  = None
    if sunrise_az is not None:
        alignment_delta  = round(sunrise_az - RINCONADA_ALIGNMENT_AZ, 2)
        alignment_active = abs(alignment_delta) <= ALIGNMENT_TOLERANCE

    # ── Upcoming solar events ─────────────────────────────────────────────────
    events = {
        "vernal_equinox":   fmt_event(ephem.next_vernal_equinox(now_utc),   now_naive),
        "summer_solstice":  fmt_event(ephem.next_summer_solstice(now_utc),  now_naive),
        "autumnal_equinox": fmt_event(ephem.next_autumnal_equinox(now_utc), now_naive),
        "winter_solstice":  fmt_event(ephem.next_winter_solstice(now_utc),  now_naive),
    }

    return {
        "generated":  now_utc.isoformat(),
        "location":   OBSERVER,
        "sun": {
            "azimuth":          round(sun_az,  2),
            "altitude":         round(sun_alt, 2),
            "compass":          compass(sun_az),
            "above_horizon":    sun_alt > 0,
            "sunrise_azimuth":  round(sunrise_az, 2) if sunrise_az is not None else None,
            "sunset_azimuth":   round(sunset_az,  2) if sunset_az  is not None else None,
        },
        "moon": {
            "azimuth":        round(moon_az,   2),
            "altitude":       round(moon_alt,  2),
            "compass":        compass(moon_az),
            "above_horizon":  moon_alt > 0,
            "phase_pct":      round(moon.phase, 1),
            "phase_name":     moon_phase_name(moon.phase),
        },
        "alignments": {
            "rinconada_active":    alignment_active,
            "rinconada_delta_deg": alignment_delta,
            "rinconada_target_az": RINCONADA_ALIGNMENT_AZ,
            "tolerance_deg":       ALIGNMENT_TOLERANCE,
            "current_sunrise_az":  round(sunrise_az, 2) if sunrise_az is not None else None,
            "note": (
                "Summer solstice sunrise aligns with Casa Rinconada's NE doorway "
                "when sunrise azimuth ≈ 66.5° (Sofaer, Sinclair & Doggett 1982)."
            ),
        },
        "events": events,
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print("SOLSTICE — astronomy fetch")
    print("=" * 42)

    data = compute()

    out = Path("data/solstice.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(data, indent=2))

    gen = datetime.fromisoformat(data["generated"])
    sun = data["sun"]
    moon = data["moon"]
    align = data["alignments"]

    print(f"  Generated : {gen.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Sun       : az {sun['azimuth']}°  alt {sun['altitude']}°  "
          f"({'above' if sun['above_horizon'] else 'below'} horizon)")
    print(f"  Moon      : az {moon['azimuth']}°  alt {moon['altitude']}°  "
          f"{moon['phase_name']} ({moon['phase_pct']}%)")
    if align["current_sunrise_az"]:
        print(f"  Sunrise   : {align['current_sunrise_az']}°  "
              f"(target 66.5°, Δ {align['rinconada_delta_deg']:+.1f}°)")
    print(f"  Alignment : {'⊕ ACTIVE' if align['rinconada_active'] else '○ inactive'}")
    print(f"\n✓ Wrote {out}")


if __name__ == "__main__":
    main()
