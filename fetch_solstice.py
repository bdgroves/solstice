#!/usr/bin/env python3
"""
SOLSTICE: the sky over Chaco Canyon, computed against the real skyline.

Writes data/sky.json once a day (GitHub Actions). The page computes the
live sun and moon itself; this file carries what needs the terrain:

  * today's sunrise and sunset at each site, over the real skyline from
    data/horizons.json (canyon walls, mesas, distant ridges), not a flat
    horizon
  * a year of sunrise and sunset points for the horizon calendar
  * the solstice extremes at each site
  * the Moon: phase (waxing or waning), and where we are in the 18.6-year
    cycle of lunar standstills
  * the next solstices and equinoxes, in Chaco's local time

    pixi run fetch            # today
    python fetch_solstice.py --date 2027-06-21
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import ephem

TZ = ZoneInfo("America/Denver")          # Chaco keeps Mountain Time with DST
OBLIQUITY = 23.44
LUNAR_INCLINATION = 5.145
HORIZONS = Path("data/horizons.json")
SITES = Path("data/sites.json")
OUT = Path("data/sky.json")


# ── geometry helpers ──────────────────────────────────────────────────────────
class Skyline:
    def __init__(self, rec: dict, step: float):
        self.alt = [v / 100.0 for v in rec["alt_cdeg"]]
        self.step = step

    def at(self, az: float) -> float:
        x = (az % 360.0) / self.step
        i = int(x)
        w = x - i
        n = len(self.alt)
        return self.alt[i % n] * (1 - w) + self.alt[(i + 1) % n] * w


def observer(lat: float, lon: float, elev: float, when: datetime) -> ephem.Observer:
    o = ephem.Observer()
    o.lat, o.lon, o.elevation = str(lat), str(lon), elev
    o.pressure = 0          # we apply refraction ourselves via the apparent altitude below
    o.date = ephem.Date(when.astimezone(timezone.utc).replace(tzinfo=None))
    return o


def refraction_deg(alt: float) -> float:
    """Bennett's formula (degrees), for a body at true altitude alt."""
    if alt < -1.5:
        return 0.0
    return 1.02 / math.tan(math.radians(alt + 10.3 / (alt + 5.11))) / 60.0


def sun_altaz(o: ephem.Observer, when: datetime) -> tuple[float, float]:
    o.date = ephem.Date(when.astimezone(timezone.utc).replace(tzinfo=None))
    s = ephem.Sun(o)
    alt = math.degrees(s.alt)
    return alt + refraction_deg(alt), math.degrees(s.az)


def crossing(o, day: date, sky: Skyline | None, rising: bool, limb: float = 0.27):
    """First (rising) or last (setting) minute the Sun's upper limb clears the skyline.

    sky=None means a flat, sea-level horizon (altitude 0). Returns (local time,
    azimuth, skyline altitude) or None if the sun never clears it.
    """
    start = datetime(day.year, day.month, day.day, tzinfo=TZ)
    # coarse scan every 4 min, then bisect to a few seconds
    def above(t):
        alt, az = sun_altaz(o, t)
        h = sky.at(az) if sky else 0.0
        return alt + limb - h, az, h
    times = [start + timedelta(minutes=k) for k in range(0, 24 * 60 + 1, 4)]
    vals = [above(t)[0] for t in times]
    idx = range(len(times) - 1)
    if not rising:
        idx = reversed(list(idx))
    for i in idx:
        a, b = vals[i], vals[i + 1]
        if (rising and a <= 0 < b) or (not rising and a > 0 >= b):
            lo, hi = times[i], times[i + 1]
            for _ in range(10):
                mid = lo + (hi - lo) / 2
                v = above(mid)[0]
                if (v > 0) == rising:
                    hi = mid
                else:
                    lo = mid
            t = hi if rising else lo
            _, az, h = above(t)
            return t, az, h
    return None


def fmt_t(t: datetime | None) -> str | None:
    return t.astimezone(TZ).strftime("%H:%M") if t else None


# ── the Moon ──────────────────────────────────────────────────────────────────
def moon_phase(now: datetime) -> dict:
    d = ephem.Date(now.astimezone(timezone.utc).replace(tzinfo=None))
    m, s = ephem.Moon(d), ephem.Sun(d)
    elong = (math.degrees(ephem.Ecliptic(m).lon) - math.degrees(ephem.Ecliptic(s).lon)) % 360
    illum = m.phase
    waxing = elong < 180
    if illum < 2:
        name = "New Moon"
    elif illum > 98:
        name = "Full Moon"
    elif abs(illum - 50) <= 6:
        name = "First Quarter" if waxing else "Last Quarter"
    elif illum < 50:
        name = "Waxing Crescent" if waxing else "Waning Crescent"
    else:
        name = "Waxing Gibbous" if waxing else "Waning Gibbous"
    prev_new = ephem.previous_new_moon(d)
    return {"name": name, "illumination_pct": round(illum, 1), "waxing": waxing,
            "elongation_deg": round(elong, 1), "age_days": round(d - prev_new, 1),
            "next_full": iso_local(ephem.next_full_moon(d)), "next_new": iso_local(ephem.next_new_moon(d))}


def node_longitude(t: datetime) -> float:
    """Mean longitude of the Moon's ascending node, degrees (Meeus 47.7)."""
    jd = ephem.julian_date(ephem.Date(t.astimezone(timezone.utc).replace(tzinfo=None)))
    T = (jd - 2451545.0) / 36525.0
    return (125.04452 - 1934.136261 * T + 0.0020708 * T * T) % 360.0


def standstill_cycle(now: datetime) -> dict:
    """Where we are in the 18.6-year cycle. Major standstill: node at 0 deg."""
    period = 18.6129 * 365.2422
    om = node_longitude(now)
    # The node regresses (its longitude decreases), so the time since it was at
    # 0 deg is (360 - om) / 360 of a cycle.
    days_since_major = ((360.0 - om) % 360.0) / 360.0 * period
    last_major = now - timedelta(days=days_since_major)
    next_major = last_major + timedelta(days=period)
    minor = last_major + timedelta(days=period / 2)
    next_minor = minor if minor > now else minor + timedelta(days=period)
    amp = OBLIQUITY + LUNAR_INCLINATION * math.cos(math.radians(om))
    return {
        "node_longitude_deg": round(om, 2),
        "cycle_fraction": round(days_since_major / period, 4),   # 0 = major, 0.5 = minor
        "max_declination_deg": round(amp, 2),                     # how far north/south the Moon reaches now
        "major_max_deg": round(OBLIQUITY + LUNAR_INCLINATION, 2),
        "minor_max_deg": round(OBLIQUITY - LUNAR_INCLINATION, 2),
        "last_major": last_major.astimezone(TZ).strftime("%B %Y"),
        "next_minor": next_minor.astimezone(TZ).strftime("%B %Y"),
        "next_major": next_major.astimezone(TZ).strftime("%B %Y"),
        "note": "Mean-node estimate; the Moon's actual extremes fall within a few months of these dates.",
    }


def rise_azimuth_for_declination(lat: float, dec: float, sky: Skyline | None) -> float:
    """Azimuth where a body of fixed declination rises over the skyline (iterative)."""
    phi, d = math.radians(lat), math.radians(dec)
    h = 0.0
    az = 90.0
    for _ in range(8):
        hr = math.radians(h)
        cos_az = (math.sin(d) - math.sin(phi) * math.sin(hr)) / (math.cos(phi) * math.cos(hr))
        az = math.degrees(math.acos(max(-1, min(1, cos_az))))
        h = (sky.at(az) if sky else 0.0) - refraction_deg(sky.at(az) if sky else 0.0)
    return az


# ── seasons ───────────────────────────────────────────────────────────────────
def iso_local(d) -> str:
    return ephem.Date(d).datetime().replace(tzinfo=timezone.utc).astimezone(TZ).isoformat(timespec="minutes")


def seasons(now: datetime) -> list[dict]:
    d = ephem.Date(now.astimezone(timezone.utc).replace(tzinfo=None))
    ev = [("Spring equinox", ephem.next_vernal_equinox(d)), ("Summer solstice", ephem.next_summer_solstice(d)),
          ("Fall equinox", ephem.next_autumnal_equinox(d)), ("Winter solstice", ephem.next_winter_solstice(d))]
    out = []
    for name, e in sorted(ev, key=lambda x: x[1]):
        t = ephem.Date(e).datetime().replace(tzinfo=timezone.utc)
        out.append({"name": name, "local": t.astimezone(TZ).isoformat(timespec="minutes"),
                    "days": (t.astimezone(TZ).date() - now.astimezone(TZ).date()).days})
    return out


# ── main ──────────────────────────────────────────────────────────────────────
def compute(now: datetime) -> dict:
    hz = json.loads(HORIZONS.read_text())
    step = hz["step_deg"]
    sites = {s["id"]: s for s in json.loads(SITES.read_text())["sites"]}
    today = now.astimezone(TZ).date()
    year = today.year
    ss = ephem.Date(ephem.next_summer_solstice(ephem.Date(f"{year}/1/1"))).datetime().date()
    ws = ephem.Date(ephem.next_winter_solstice(ephem.Date(f"{year}/1/1"))).datetime().date()
    out_sites = []
    for rec in hz["sites"]:
        sky = Skyline(rec, step)
        o = observer(rec["lat"], rec["lon"], rec["ground_m"] + rec["eye_m"], now)

        def rs(day):
            r, s = crossing(o, day, sky, True), crossing(o, day, sky, False)
            fr, fs = crossing(o, day, None, True), crossing(o, day, None, False)
            return r, s, fr, fs

        r, s, fr, fs = rs(today)
        cal = []
        d = date(year, 1, 1)
        while d.year == year:          # every 3rd day keeps the file small; the page interpolates
            rr, st = crossing(o, d, sky, True), crossing(o, d, sky, False)
            cal.append([d.timetuple().tm_yday, round(rr[1], 2) if rr else None, round(rr[2], 2) if rr else None,
                        round(st[1], 2) if st else None, round(st[2], 2) if st else None])
            d += timedelta(days=3)
        ext = {}
        for label, day in (("summer", ss), ("winter", ws)):
            a, b = crossing(o, day, sky, True), crossing(o, day, sky, False)
            fa = crossing(o, day, None, True)
            ext[label] = {"date": day.isoformat(), "sunrise_az": round(a[1], 2), "sunrise_alt": round(a[2], 2),
                          "sunrise_time": fmt_t(a[0]), "sunset_az": round(b[1], 2), "sunset_time": fmt_t(b[0]),
                          "flat_sunrise_az": round(fa[1], 2)}
        moon_lim = {k: round(rise_azimuth_for_declination(rec["lat"], v, sky), 2)
                    for k, v in (("major_north", OBLIQUITY + LUNAR_INCLINATION),
                                 ("minor_north", OBLIQUITY - LUNAR_INCLINATION),
                                 ("minor_south", -(OBLIQUITY - LUNAR_INCLINATION)),
                                 ("major_south", -(OBLIQUITY + LUNAR_INCLINATION)))}
        out_sites.append({
            "id": rec["id"], "name": rec["name"],
            "today": {
                "sunrise": fmt_t(r[0]) if r else None, "sunrise_az": round(r[1], 2) if r else None,
                "sunrise_skyline_alt": round(r[2], 2) if r else None,
                "sunset": fmt_t(s[0]) if s else None, "sunset_az": round(s[1], 2) if s else None,
                "sunset_skyline_alt": round(s[2], 2) if s else None,
                "flat_sunrise": fmt_t(fr[0]) if fr else None, "flat_sunset": fmt_t(fs[0]) if fs else None,
                "minutes_lost_to_skyline": (round(((r[0] - fr[0]).total_seconds() + (fs[0] - s[0]).total_seconds()) / 60)
                                            if r and s and fr and fs else None),
            },
            "solstices": ext,
            "moonrise_limits_az": moon_lim,
            "calendar": cal,   # [day_of_year, sunrise_az, skyline_alt, sunset_az, skyline_alt]
        })
    # where today's sunrise sits between the two solstice extremes, at Pueblo Bonito
    bon = next(x for x in out_sites if x["id"] == "pueblo-bonito")
    s_az, w_az, t_az = (bon["solstices"]["summer"]["sunrise_az"], bon["solstices"]["winter"]["sunrise_az"],
                        bon["today"]["sunrise_az"])
    return {
        "generated": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "date": today.isoformat(),
        "timezone": "America/Denver",
        "sites": out_sites,
        "sun_journey": {"site": "pueblo-bonito", "fraction_toward_summer": round((w_az - t_az) / (w_az - s_az), 3),
                        "heading": "north" if (today.timetuple().tm_yday < ss.timetuple().tm_yday
                                               or today.timetuple().tm_yday > ws.timetuple().tm_yday) else "south"},
        "moon": moon_phase(now),
        "standstill": standstill_cycle(now),
        "seasons": seasons(now),
        "method": {
            "skyline": "USGS 3DEP elevation: CONMGaps lidar at 1 m from 80 m to 2.4 km of each site, then 5 m and 30 m; 0.25 deg azimuth steps, Earth curvature, refraction k=0.13",
            "sun": "PyEphem positions; Bennett refraction; sunrise = upper limb clears the skyline",
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (local) to compute for; default now")
    a = ap.parse_args()
    now = (datetime.fromisoformat(a.date).replace(hour=12, tzinfo=TZ) if a.date
           else datetime.now(timezone.utc))
    data = compute(now)
    OUT.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
    b = next(s for s in data["sites"] if s["id"] == "pueblo-bonito")
    print(f"SOLSTICE {data['date']}  Pueblo Bonito sunrise {b['today']['sunrise']} at {b['today']['sunrise_az']}° "
          f"(flat horizon {b['today']['flat_sunrise']}); summer solstice sunrise az "
          f"{b['solstices']['summer']['sunrise_az']}°, winter {b['solstices']['winter']['sunrise_az']}°")
    print(f"Moon {data['moon']['name']} {data['moon']['illumination_pct']}%  ·  standstill cycle "
          f"{data['standstill']['cycle_fraction']:.2f} (last major {data['standstill']['last_major']}, "
          f"next minor {data['standstill']['next_minor']})")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
