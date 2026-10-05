"""Look up Chaco's archaeological sites in OpenStreetMap and cut 0.6 m NAIP
crops around each, to check the coordinates SOLSTICE uses. Runs on Actions."""
import json
from pathlib import Path

import numpy as np
import planetary_computer
import pystac_client
import rasterio
import requests
from PIL import Image, ImageDraw
from rasterio.warp import transform

OUT = Path("osm"); OUT.mkdir(exist_ok=True)
BBOX = (35.99, -108.08, 36.16, -107.84)  # S, W, N, E
q = f"""[out:json][timeout:120];
(nwr["historic"="archaeological_site"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
 nwr["natural"="peak"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
 nwr["tourism"="attraction"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]}););
out center tags;"""
els = []
for ep in ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]:
    try:
        r = requests.post(ep, data={"data": q}, timeout=180, headers={"User-Agent": "solstice (github.com/bdgroves/solstice)"})
        r.raise_for_status(); els = r.json()["elements"]; break
    except Exception as e:
        print(ep, e)
rows = []
for e in els:
    lat = e.get("lat") or e.get("center", {}).get("lat"); lon = e.get("lon") or e.get("center", {}).get("lon")
    rows.append({"name": e.get("tags", {}).get("name"), "lat": lat, "lon": lon, "type": e["type"], "id": e["id"],
                 "tags": {k: v for k, v in e.get("tags", {}).items() if k in ("historic", "natural", "tourism", "site_type", "wikidata", "ele")}})
(OUT / "osm_sites.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
print(len(rows), "features"); [print(r["name"], r["lat"], r["lon"]) for r in rows if r["name"]]

cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=planetary_computer.sign_inplace)
items = [i for i in cat.search(collections=["naip"], bbox=(-108.08, 35.99, -107.84, 36.16)).items() if i.datetime.year == 2022]
want = [r for r in rows if r["name"] and r["tags"].get("historic")]
for r in want:
    for it in items:
        with rasterio.open(it.assets["image"].href) as src:
            (x,), (y,) = transform("EPSG:4326", src.crs, [r["lon"]], [r["lat"]])
            row, col = src.index(x, y)
            if 0 <= row < src.height and 0 <= col < src.width:
                win = rasterio.windows.Window(col - 250, row - 250, 500, 500)  # 300 m
                a = src.read([1, 2, 3], window=win, boundless=True)
                im = Image.fromarray(np.moveaxis(a, 0, -1)); d = ImageDraw.Draw(im)
                d.line([(250, 235), (250, 265)], fill=(255, 0, 0)); d.line([(235, 250), (265, 250)], fill=(255, 0, 0))
                fn = "".join(ch if ch.isalnum() else "_" for ch in r["name"])[:40]
                im.save(OUT / f"{fn}_{r['id']}.jpg", quality=85)
                break
