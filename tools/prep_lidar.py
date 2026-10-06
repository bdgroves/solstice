"""
Half-metre bare earth for Chaco Canyon, from the raw USGS point clouds.

USGS's finished 1 m DEM has a hole over the canyon core (around Pueblo Bonito),
and the 2018 survey behind the rest of it is sparse. A newer gap-fill survey,
CO_CONMGaps_D24, was flown over the canyon but only published as point-cloud
tiles. This downloads those tiles, keeps the returns classified as ground, and
grids each 1 km tile to 0.5 m in UTM 13N. (After project-kiva's kiva/fetch.py.)

Writes to prep-lidar/:
  <tile>.tif        0.5 m ground elevation (float32, NaN where no ground return
                    fell within GAP_M), UTM 13N
  index.json        each tile's bounds, ground-point count and density

Runs on GitHub Actions (prep-lidar.yml) and needs PDAL.
"""
from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from scipy.ndimage import distance_transform_edt

from prep_data import RENDER_LL, UTM, http_get

TNM = "https://tnmaccess.nationalmap.gov/api/v1/products"
ROCKY = "https://rockyweb.usgs.gov/vdelivery/Datasets/Staged/"
S3 = "https://prd-tnm.s3.amazonaws.com/StagedProducts/"
PROJECT = "CONMGaps"        # the survey we want (newest, densest)
RES = 0.5                   # metres
GAP_M = 3.0                 # leave holes wider than this as NaN
NEAR_KM = 1.5               # keep tiles within this distance of a site: the
                            # survey covers the whole render extent (hundreds of
                            # tiles), but the fine layers only look close up
OUT = Path("prep-lidar")
CACHE = Path("laz")


def near_sites(item, sites):
    bb = item.get("boundingBox") or {}
    if not bb:
        return True
    lon, lat = (bb["minX"] + bb["maxX"]) / 2, (bb["minY"] + bb["maxY"]) / 2
    for s in sites:
        dx = (lon - s["lon"]) * 111.32 * math.cos(math.radians(lat))
        dy = (lat - s["lat"]) * 110.57
        if math.hypot(dx, dy) <= NEAR_KM + 0.71:     # + half a 1 km tile's diagonal
            return True
    return False


def tiles():
    sites = json.loads((Path(__file__).resolve().parent.parent / "data/sites.json").read_text())["sites"]
    out, offset, seen = [], 0, 0
    while True:
        code, body = http_get(TNM, {"datasets": "Lidar Point Cloud (LPC)", "prodFormats": "LAZ",
                                    "bbox": ",".join(map(str, RENDER_LL)), "outputFormat": "JSON",
                                    "max": 200, "offset": offset}, timeout=120)
        if code != 200:
            raise SystemExit(f"TNM: HTTP {code} {body[:200]!r}")
        j = json.loads(body)
        items = j.get("items", [])
        for it in items:
            url = it.get("downloadURL") or ""
            m = re.search(r"/Projects/([^/]+)/", url)
            if url.lower().endswith(".laz") and m and PROJECT in m.group(1):
                seen += 1
                if not near_sites(it, sites):
                    continue
                out.append({"url": url, "project": m.group(1), "bytes": it.get("sizeInBytes", 0),
                            "published": it.get("publicationDate", "")})
        offset += len(items)
        if not items or offset >= j.get("total", 0):
            break
    print(f"{PROJECT}: {seen} tiles over the render extent, {len(out)} within {NEAR_KM} km of a site", flush=True)
    return out


def download(url, dst: Path):
    if dst.exists():
        return
    tmp = dst.with_suffix(".part")
    for u in ([url.replace(ROCKY, S3)] if url.startswith(ROCKY) else []) + [url]:
        import urllib.request
        try:
            t0 = time.time()
            with urllib.request.urlopen(u, timeout=300) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            tmp.rename(dst)
            print(f"  {dst.name}: {dst.stat().st_size / 1e6:.0f} MB in {time.time() - t0:.0f} s", flush=True)
            return
        except Exception as e:  # noqa: BLE001 - try the next mirror
            print(f"  {dst.name}: {u.split('/')[2]} failed ({e})", flush=True)
    raise SystemExit(f"could not download {url}")


def grid(laz: Path, tif: Path) -> dict:
    import pdal
    raw = tif.with_suffix(".raw.tif")
    pipe = [{"type": "readers.las", "filename": str(laz)},
            {"type": "filters.range", "limits": "Classification[2:2]"},
            {"type": "filters.reprojection", "out_srs": UTM},
            {"type": "writers.gdal", "filename": str(raw), "resolution": RES,
             "output_type": "idw,count", "radius": RES * 2.5, "window_size": 3,
             "nodata": -9999, "data_type": "float32"}]
    n = pdal.Pipeline(json.dumps({"pipeline": pipe})).execute()
    with rasterio.open(raw) as s:
        z, cnt, prof = s.read(1), s.read(2), s.profile
    z = np.where((z == -9999) | ~np.isfinite(z), np.nan, z).astype("float32")
    has = (cnt > 0) & np.isfinite(z)
    # fill small holes from the nearest ground cell; leave wide ones empty
    dist, (ri, ci) = distance_transform_edt(~has, return_indices=True)
    z = np.where(dist * RES <= GAP_M, z[ri, ci], np.nan).astype("float32")
    prof.update(count=1, dtype="float32", nodata=np.nan, compress="deflate", predictor=3,
                tiled=True, blockxsize=512, blockysize=512)
    with rasterio.open(tif, "w", **prof) as d:
        d.write(z, 1)
    raw.unlink()
    b = rasterio.open(tif).bounds
    area = (b.right - b.left) * (b.top - b.bottom)
    return {"file": tif.name, "bounds": [b.left, b.bottom, b.right, b.top], "ground_points": int(n),
            "density_per_m2": round(n / area, 2), "coverage": round(float(np.isfinite(z).mean()), 4)}


def main():
    OUT.mkdir(exist_ok=True)
    CACHE.mkdir(exist_ok=True)
    ts = tiles()
    print(f"{len(ts)} {PROJECT} tiles over the render extent, {sum(t['bytes'] for t in ts) / 1e9:.2f} GB", flush=True)
    if not ts:
        raise SystemExit("no tiles")
    dsts = [CACHE / t["url"].rsplit("/", 1)[-1] for t in ts]
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda td: download(td[0]["url"], td[1]), zip(ts, dsts)))
    from concurrent.futures import ProcessPoolExecutor
    index = []
    with ProcessPoolExecutor(4) as pool:
        for t, rec in zip(ts, pool.map(grid, dsts, [OUT / (laz.stem + ".tif") for laz in dsts])):
            rec.update(project=t["project"], published=t["published"], source=t["url"])
            index.append(rec)
            print(f"  {rec['file']}: {rec['ground_points']:,} ground returns, {rec['density_per_m2']}/m², "
                  f"{rec['coverage']:.1%} covered", flush=True)
            (CACHE / (Path(rec["source"]).name)).unlink(missing_ok=True)
    (OUT / "index.json").write_text(json.dumps({
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": PROJECT, "res_m": RES, "crs": UTM, "tiles": index}, indent=1))
    print(f"wrote {len(index)} tiles to {OUT}/")


if __name__ == "__main__":
    main()
