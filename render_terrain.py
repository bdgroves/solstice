#!/usr/bin/env python3
"""
SOLSTICE — render_terrain.py

One-time terrain render of Chaco Canyon using forge3d.
Clips the full USGS DEM tile to just the canyon core first
(dramatically reduces GPU load), then renders with forge3d.

Requirements:
    pip install forge3d
    pixi install   (includes rasterio for DEM clipping)

Usage:
    python render_terrain.py
"""

import sys
import time
import requests
from pathlib import Path

BBOX = {
    "west":  -108.05,
    "south":  36.05,
    "east":  -107.85,
    "north":  36.15,
}

# Tight clip — just the canyon itself, much smaller GPU load
CLIP_BBOX = {
    "west":  -107.99,
    "south":  36.02,
    "east":  -107.88,
    "north":  36.13,
}

DEM_PATH    = Path("data/chaco_dem.tif")
CLIP_PATH   = Path("data/chaco_canyon_clip.tif")
RENDER_PATH = Path("assets/terrain.png")


# ── Download full DEM ─────────────────────────────────────────────────────────

def download_dem():
    if DEM_PATH.exists():
        size_mb = DEM_PATH.stat().st_size / 1_048_576
        print(f"✓ DEM already exists: {DEM_PATH} ({size_mb:.1f} MB)")
        return

    DEM_PATH.parent.mkdir(exist_ok=True)
    api_url = "https://tnmaccess.nationalmap.gov/api/v1/products"

    for dataset in [
        "Digital Elevation Model (DEM) 1/3 arc-second",
        "National Elevation Dataset (NED) 1/3 arc-second",
        "Digital Elevation Model (DEM) 1 arc-second",
    ]:
        print(f"→ Querying USGS National Map: {dataset} …")
        params = {
            "datasets":     dataset,
            "bbox":         f"{BBOX['west']},{BBOX['south']},{BBOX['east']},{BBOX['north']}",
            "max":          5,
            "outputFormat": "JSON",
        }
        try:
            r = requests.get(api_url, params=params, timeout=30)
            r.raise_for_status()
            items = r.json().get("items", [])
            if items:
                break
        except Exception as exc:
            print(f"  ⚠  Query failed: {exc}")
            items = []
    else:
        print("\n✗ No DEM tiles found."); sys.exit(1)

    tile   = items[0]
    dl_url = tile.get("downloadURL")
    print(f"→ Downloading: {tile.get('title', 'DEM tile')}")

    with requests.get(dl_url, stream=True, timeout=300) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(DEM_PATH, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65_536):
                fh.write(chunk)
                downloaded += len(chunk)
                if total:
                    print(f"\r  {downloaded/total*100:5.1f}%  {downloaded//1_048_576} / {total//1_048_576} MB",
                          end="", flush=True)
        print()
    print(f"✓ Saved: {DEM_PATH} ({DEM_PATH.stat().st_size/1_048_576:.1f} MB)")


# ── Clip DEM to canyon core ───────────────────────────────────────────────────

def clip_dem():
    if CLIP_PATH.exists():
        size_mb = CLIP_PATH.stat().st_size / 1_048_576
        print(f"✓ Clipped DEM already exists: {CLIP_PATH} ({size_mb:.1f} MB)")
        return

    try:
        import rasterio
        from rasterio.mask import mask
        from shapely.geometry import box
    except ImportError:
        print("✗ rasterio not installed. Run: pixi install")
        sys.exit(1)

    print(f"→ Clipping DEM to canyon core …")
    print(f"  {CLIP_BBOX['west']}°W – {CLIP_BBOX['east']}°W, "
          f"{CLIP_BBOX['south']}°N – {CLIP_BBOX['north']}°N")

    clip_geom = box(CLIP_BBOX["west"], CLIP_BBOX["south"],
                    CLIP_BBOX["east"],  CLIP_BBOX["north"])

    with rasterio.open(DEM_PATH) as src:
        out_image, out_transform = mask(src, [clip_geom], crop=True)
        out_meta = src.meta.copy()
        out_meta.update({
            "driver": "GTiff",
            "height": out_image.shape[1],
            "width":  out_image.shape[2],
            "transform": out_transform,
        })
        with rasterio.open(CLIP_PATH, "w", **out_meta) as dest:
            dest.write(out_image)

    size_mb = CLIP_PATH.stat().st_size / 1_048_576
    print(f"✓ Clipped DEM saved: {CLIP_PATH} ({size_mb:.1f} MB)")


# ── Render with forge3d ────────────────────────────────────────────────────────

def render_terrain():
    try:
        import forge3d
    except ImportError:
        print("\n✗ forge3d not installed.  pip install forge3d"); sys.exit(1)

    RENDER_PATH.parent.mkdir(exist_ok=True)

    # Use the clipped DEM if available, fall back to full tile
    dem = CLIP_PATH if CLIP_PATH.exists() else DEM_PATH
    size_mb = dem.stat().st_size / 1_048_576
    print(f"\n→ Rendering: {dem} ({size_mb:.1f} MB)")
    print("  (A viewer window will appear briefly — that's normal)")

    with forge3d.open_viewer_async(terrain_path=str(dem)) as viewer:
        viewer.set_orbit_camera(phi_deg=210, theta_deg=28, radius=1.1)
        viewer.set_sun(azimuth_deg=240, elevation_deg=20)

        # Scale wait time to file size — ~1s per 10 MB, min 8s
        wait = max(8, int(size_mb / 10))
        print(f"→ Waiting {wait}s for terrain to load into GPU …")
        time.sleep(wait)

        for width, height, label in [(3840, 2160, "4K"), (2560, 1440, "1440p"), (1920, 1080, "1080p")]:
            print(f"→ Snapping {label} ({width}×{height}) …")
            viewer.snapshot(str(RENDER_PATH), width=width, height=height)
            time.sleep(3)
            size = RENDER_PATH.stat().st_size if RENDER_PATH.exists() else 0
            if size > 500_000:   # >500 KB = definitely real terrain content
                print(f"  ✓ {size // 1024} KB — success at {label}")
                break
            print(f"  ⚠ Got {size // 1024} KB — might be a blank frame, retrying …")
        else:
            print("\n✗ Could not get a valid render at any resolution.")
            print("  The clipped DEM and wait time may need further tuning.")
            sys.exit(1)

    print(f"\n✓ Saved: {RENDER_PATH} ({RENDER_PATH.stat().st_size // 1024} KB)")
    print("\nNext steps:\n  git add assets/terrain.png\n  git commit -m 'feat: Chaco Canyon terrain render'\n  git push")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print("SOLSTICE — Terrain Render Script (forge3d)")
    print("=" * 44)
    download_dem()
    print()
    clip_dem()
    print()
    render_terrain()

if __name__ == "__main__":
    main()
