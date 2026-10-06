"""
Fetch the terrain and imagery SOLSTICE renders from. Runs on GitHub Actions
(workflow: prep-data.yml), which can reach the data services.

Writes to prep/:
  chaco_dem_5m.tif        USGS 3DEP best-available elevation (lidar where
                          flown), 5 m, UTM 13N, over the canyon and the start
                          of the North Road
  chaco_naip_2p5m.tif     USGS NAIP aerial photo mosaic, 2.5 m, same grid x2
  horizon_dem_30m.tif     3DEP, 30 m, ~60 km around the canyon, for computing
                          the real skyline each site sees
  sources.json            what was fetched, from where, when
"""
from __future__ import annotations

import json
import time
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform_bounds

OUT = Path("prep")
UTM = "EPSG:26913"   # NAD83 / UTM 13N
IMAGESERVER = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"

# Render extent (lon/lat): Penasco Blanco to Wijiji, the south mesa to the
# first miles of the North Road.
RENDER_LL = (-108.08, 35.99, -107.84, 36.16)
# Horizon extent: ~60 km each way.
HORIZON_LL = (-108.62, 35.58, -107.30, 36.55)


def http_get(url, params=None, data=None, timeout=300):
    """(status, body bytes). Standard library only, so the renders run in any
    Python that has rasterio (no requests needed)."""
    import urllib.error
    import urllib.parse
    import urllib.request
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "solstice-render",
                                 **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def utm_box(ll, step):
    x0, y0, x1, y1 = transform_bounds("EPSG:4326", UTM, *ll, densify_pts=21)
    x0, y0 = math.floor(x0 / step) * step, math.floor(y0 / step) * step
    x1, y1 = math.ceil(x1 / step) * step, math.ceil(y1 / step) * step
    return x0, y0, x1, y1


def fetch_dem(ll, step, name, tile=2000):
    return fetch_dem_utm(utm_box(ll, step), step, OUT / name, tile)


def fetch_dem_utm(bounds, step, path, tile=2000):
    """3DEP best-available elevation over a UTM 13N box at `step` metres."""
    name = Path(path).name
    x0, y0, x1, y1 = bounds
    w, h = int(round((x1 - x0) / step)), int(round((y1 - y0) / step))
    out = np.full((h, w), np.nan, dtype="float32")
    for r0 in range(0, h, tile):
        for c0 in range(0, w, tile):
            tw, th = min(tile, w - c0), min(tile, h - r0)
            bx0 = x0 + c0 * step
            by1 = y1 - r0 * step
            bbox = f"{bx0},{by1 - th * step},{bx0 + tw * step},{by1}"
            params = dict(bbox=bbox, bboxSR=26913, imageSR=26913, size=f"{tw},{th}",
                          format="tiff", pixelType="F32", noData=-9999,
                          interpolation="RSP_BilinearInterpolation", f="image")
            for attempt in range(5):
                try:
                    code, body = http_get(IMAGESERVER, params)
                except OSError as e:
                    code, body = 0, str(e).encode()
                if code == 200 and body[:2] in (b"II", b"MM"):
                    break
                print(f"  retry {name} tile {r0},{c0}: HTTP {code} {body[:80]!r}", flush=True)
                time.sleep(3 * (attempt + 1))
            else:
                raise RuntimeError(f"3DEP tile failed {r0},{c0}")
            with rasterio.MemoryFile(body) as mf, mf.open() as src:
                a = src.read(1).astype("float32")
                a[a < -1000] = np.nan
                out[r0:r0 + a.shape[0], c0:c0 + a.shape[1]] = a[:th, :tw]
            print(f"  {name}: tile {r0},{c0} ok")
    prof = dict(driver="GTiff", width=w, height=h, count=1, dtype="float32", crs=UTM,
                transform=from_origin(x0, y1, step, step), nodata=np.nan,
                compress="deflate", predictor=3, tiled=True)
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(out, 1)
    print(f"wrote {path} {w}x{h} min {np.nanmin(out):.1f} max {np.nanmax(out):.1f} nan {np.isnan(out).mean():.4f}")
    return path


def fetch_naip(ll, step, name):
    import planetary_computer
    import pystac_client
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1",
                                    modifier=planetary_computer.sign_inplace)
    items = list(cat.search(collections=["naip"], bbox=ll).items())
    years = sorted({i.datetime.year if i.datetime else int(str(i.properties.get("naip:year"))) for i in items})
    print("NAIP years available:", years)
    best = max(years)
    items = [i for i in items if (i.datetime.year if i.datetime else int(i.properties["naip:year"])) == best]
    print(f"Using NAIP {best}: {len(items)} quarter-quads")
    x0, y0, x1, y1 = utm_box(ll, step)
    w, h = int((x1 - x0) / step), int((y1 - y0) / step)
    dst_t = from_origin(x0, y1, step, step)
    mosaic = np.zeros((3, h, w), dtype="uint8")
    filled = np.zeros((h, w), dtype=bool)
    for it in items:
        href = it.assets["image"].href
        with rasterio.open(href) as src:
            part = np.zeros((3, h, w), dtype="uint8")
            for b in range(3):
                reproject(rasterio.band(src, b + 1), part[b], src_transform=src.transform,
                          src_crs=src.crs, dst_transform=dst_t, dst_crs=UTM,
                          resampling=Resampling.average, dst_nodata=0)
            have = (part.sum(axis=0) > 0) & ~filled
            mosaic[:, have] = part[:, have]
            filled |= have
        print(f"  {it.id}: {filled.mean():.3f} filled")
    prof = dict(driver="GTiff", width=w, height=h, count=3, dtype="uint8", crs=UTM,
                transform=dst_t, compress="jpeg", jpeg_quality=90, photometric="ycbcr",
                tiled=True)
    path = OUT / name
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(mosaic)
    print(f"wrote {path} {w}x{h}")
    return path, best, [i.id for i in items]


def main():
    OUT.mkdir(exist_ok=True)
    dem = fetch_dem(RENDER_LL, 5.0, "chaco_dem_5m.tif")
    hor = fetch_dem(HORIZON_LL, 30.0, "horizon_dem_30m.tif")
    naip, year, ids = fetch_naip(RENDER_LL, 2.5, "chaco_naip_2p5m.tif")
    (OUT / "sources.json").write_text(json.dumps({
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dem": {"service": IMAGESERVER, "note": "USGS 3DEP best-available (lidar-derived where available)",
                "files": [dem.name, hor.name]},
        "naip": {"collection": "naip (Microsoft Planetary Computer)", "year": year, "items": ids},
        "crs": UTM, "render_extent_lonlat": RENDER_LL, "horizon_extent_lonlat": HORIZON_LL,
    }, indent=1))


if __name__ == "__main__":
    main()
