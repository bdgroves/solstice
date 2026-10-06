"""
Compute the real skyline seen from each Chaco site.

For every azimuth (0.25 deg steps) march outward along the ground and keep the
highest elevation angle of the terrain, with Earth curvature and standard
refraction (k = 0.13). Within 2.4 km of each site the ground comes from the
0.5 m CONMGaps lidar (prep/lidar, tools/prep_lidar.py) at 1 m where it exists;
then the 5 m 3DEP grid (canyon walls), then the 30 m one (mesas, Chacra Mesa,
distant mountains). The 5 m grid has a hole-filled patch over the canyon core,
so the lidar matters most for the great houses' own skylines.

    python tools/horizon.py --prep prep --sites data/sites.json --out data/horizons.json
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform
from scipy.ndimage import map_coordinates

R_EARTH = 6_371_000.0
K_REFR = 0.13
EYE = 1.6
STEP_DEG = 0.25


class Grid:
    def __init__(self, path):
        with rasterio.open(path) as src:
            self.z = src.read(1).astype("float64")
            self.t = src.transform
        self.z = np.where(np.isfinite(self.z), self.z, np.nan)
        self.h, self.w = self.z.shape
        self.x0, self.y1, self.dx = self.t.c, self.t.f, self.t.a

    def sample(self, x, y):
        col = (x - self.x0) / self.dx - 0.5
        row = (self.y1 - y) / self.dx - 0.5
        inside = (col >= 0) & (col <= self.w - 1) & (row >= 0) & (row <= self.h - 1)
        out = np.full(np.shape(x), np.nan)
        if inside.any():
            out[inside] = map_coordinates(self.z, [row[inside], col[inside]], order=1, mode="nearest")
        return out


class LidarPatch(Grid):
    """The lidar tiles around one site, mosaicked to 1 m."""
    def __init__(self, tiles, x, y, half=2500.0, res=1.0):
        from rasterio.merge import merge
        from rasterio.warp import Resampling
        box = (x - half, y - half, x + half, y + half)
        use = [t for t in tiles if not (t[1][2] <= box[0] or t[1][0] >= box[2] or t[1][3] <= box[1] or t[1][1] >= box[3])]
        self.ok = bool(use)
        if not use:
            return
        srcs = [rasterio.open(p) for p, _ in use]
        a, t = merge(srcs, bounds=box, res=res, nodata=np.nan, resampling=Resampling.average)
        for s_ in srcs:
            s_.close()
        self.z = a[0].astype("float64")
        self.t = t
        self.h, self.w = self.z.shape
        self.x0, self.y1, self.dx = t.c, t.f, t.a


def skyline(fine: Grid, coarse: Grid, x, y, eye=EYE, max_km=80, lidar=None):
    z0 = np.nan
    if not np.isfinite(z0):
        z0 = fine.sample(np.array([x]), np.array([y]))[0]
    if not np.isfinite(z0):
        z0 = coarse.sample(np.array([x]), np.array([y]))[0]
    z0 += eye
    # distances: dense near, sparse far
    d = np.concatenate([np.arange(8, 2000, 4), np.arange(2000, 10000, 10),
                        np.arange(10000, max_km * 1000, 30)]).astype("float64")
    az = np.arange(0, 360, STEP_DEG)
    out = np.empty(az.size)
    far_from = []
    for i, a in enumerate(np.radians(az)):
        xs, ys = x + d * math.sin(a), y + d * math.cos(a)
        z = fine.sample(xs, ys)
        zc = coarse.sample(xs, ys)
        z = np.where(np.isfinite(z), z, zc)
        if lidar is not None and lidar.ok:
            # beyond the site's own walls and rubble mound, which bare-earth
            # lidar keeps as ground (it put a 4 m "skyline" 50 m from Penasco Blanco)
            near = (d >= 80) & (d <= 2400)
            zl = lidar.sample(xs[near], ys[near])
            z[near] = np.where(np.isfinite(zl), zl, z[near])
        drop = d * d / (2 * R_EARTH) * (1 - K_REFR)
        ang = np.degrees(np.arctan2(z - z0 - drop, d))
        j = int(np.nanargmax(ang))
        out[i] = ang[j]
        far_from.append(d[j])
    return az, out, np.array(far_from), z0 - eye


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prep", default="prep")
    ap.add_argument("--sites", default="data/sites.json")
    ap.add_argument("--out", default="data/horizons.json")
    a = ap.parse_args()
    fine = Grid(Path(a.prep) / "chaco_dem_5m.tif")
    coarse = Grid(Path(a.prep) / "horizon_dem_30m.tif")
    sites = json.loads(Path(a.sites).read_text())["sites"]
    tiles = []
    idx = Path(a.prep) / "lidar" / "index.json"
    if idx.exists():
        tiles = [(Path(a.prep) / "lidar" / t["file"], t["bounds"]) for t in json.loads(idx.read_text())["tiles"]]
        print(f"lidar: {len(tiles)} tiles")
    res = []
    for s in sites:
        if not s.get("horizon"):
            continue
        (x,), (y,) = transform("EPSG:4326", "EPSG:26913", [s["lon"]], [s["lat"]])
        lid = LidarPatch(tiles, x, y) if tiles else None
        az, alt, dist, zg = skyline(fine, coarse, x, y, eye=s.get("eye_m", EYE), lidar=lid)
        res.append({
            "id": s["id"], "name": s["name"], "lat": s["lat"], "lon": s["lon"],
            "ground_m": round(float(zg), 1), "eye_m": s.get("eye_m", EYE),
            # altitude of the skyline in hundredths of a degree, az 0..359.75
            "alt_cdeg": [int(round(v * 100)) for v in alt],
            # distance to the skyline in 10 m units (where the horizon is)
            "dist_dam": [int(round(v / 10)) for v in dist],
        })
        e = slice(int(50 / STEP_DEG), int(130 / STEP_DEG))
        print(f"{s['name']:<22} ground {zg:7.1f} m  east skyline {alt[e].min():5.2f}..{alt[e].max():5.2f} deg"
              f"  (nearest edge {dist[e].min():6.0f} m)")
    Path(a.out).write_text(json.dumps({"step_deg": STEP_DEG, "refraction_k": K_REFR,
                                       "source": "USGS 3DEP: 0.5 m CONMGaps lidar (at 1 m) near the sites + 5 m + 30 m" if tiles else "USGS 3DEP 5 m + 30 m", "sites": res},
                                      separators=(",", ":")))
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
