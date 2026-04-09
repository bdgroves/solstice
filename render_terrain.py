#!/usr/bin/env python3
"""
SOLSTICE — render_terrain.py

One-time terrain render of Chaco Canyon using forge3d.
Downloads 1/3 arc-second DEM from USGS National Map (3DEP),
renders a dramatic 3D hillshade, saves to assets/terrain.png.

Run locally — NOT in GitHub Actions (requires GPU and display context).
Commit assets/terrain.png after running.

Requirements (beyond pixi environment):
    pip install forge3d

Usage:
    python render_terrain.py
"""

import sys
import time
import requests
from pathlib import Path

# ── Bounding box for Chaco Canyon area ────────────────────────────────────────

BBOX = {
    "west":  -108.05,
    "south":  36.00,
    "east":  -107.85,
    "north":  36.15,
}

DEM_PATH    = Path("data/chaco_dem.tif")
RENDER_PATH = Path("assets/terrain.png")

# ── Download DEM from USGS National Map ───────────────────────────────────────

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
        print("\n✗ No DEM tiles found via USGS API.")
        sys.exit(1)

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
    print(f"✓ DEM saved: {DEM_PATH} ({DEM_PATH.stat().st_size/1_048_576:.1f} MB)")


# ── Render with forge3d ────────────────────────────────────────────────────────

def render_terrain():
    try:
        import forge3d
    except ImportError:
        print("\n✗ forge3d not installed.  pip install forge3d")
        sys.exit(1)

    RENDER_PATH.parent.mkdir(exist_ok=True)

    print(f"\n→ Launching forge3d viewer for: {DEM_PATH}")
    print("  (A viewer window will appear briefly — that's normal)")

    with forge3d.open_viewer_async(terrain_path=str(DEM_PATH)) as viewer:
        viewer.set_orbit_camera(phi_deg=210, theta_deg=28, radius=1.1)
        viewer.set_sun(azimuth_deg=240, elevation_deg=20)

        # Give WebGPU + 366 MB DEM time to fully initialize
        print("→ Waiting for terrain to load (5s) …")
        time.sleep(5)

        # Try resolutions high → low; stop at first real result
        for width, height, label in [(3840, 2160, "4K"), (2560, 1440, "1440p"), (1920, 1080, "1080p")]:
            print(f"→ Snapping {label} ({width}×{height}) …")
            viewer.snapshot(str(RENDER_PATH), width=width, height=height)
            time.sleep(2)
            size = RENDER_PATH.stat().st_size if RENDER_PATH.exists() else 0
            if size > 50_000:
                print(f"  ✓ {size // 1024} KB — success at {label}")
                break
            print(f"  ⚠ Got {size} bytes — retrying at lower resolution …")
        else:
            print("\n✗ All resolutions produced empty output.")
            print("  Try updating GPU drivers or run: python render_terrain.py --debug")
            sys.exit(1)

    print(f"\n✓ Saved: {RENDER_PATH} ({RENDER_PATH.stat().st_size // 1024} KB)")
    print("\nNext steps:\n  git add assets/terrain.png\n  git commit -m 'feat: Chaco Canyon terrain render'\n  git push")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print("SOLSTICE — Terrain Render Script (forge3d)")
    print("=" * 44)
    download_dem()
    print()
    render_terrain()

if __name__ == "__main__":
    main()
