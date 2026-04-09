# SOLSTICE

> *"The canyon aligned its stones to the sky. We aligned our data to the canyon."*

**[brooksgroves.com/solstice](https://brooksgroves.com/solstice/)** — Live dashboard · Updated twice daily

---

![SOLSTICE Dashboard](assets/terrain.png)

---

## The Story

In the summer of A.D. 1054, a supernova blazed in the sky bright enough to cast shadows at night. Somewhere near Fajada Butte, at the eastern entrance to Chaco Canyon, a Chacoan astronomer watched it rise. Some scholars believe they marked it on the cliff face — a painted hand, a crescent, a bright disc. We don't know their name. We don't know their language. We don't know what they called the sky above them. But we know they were watching, because they built it into the stone.

Pueblo Bonito's rear wall runs true north to within a tenth of a degree. The northeast doorway of Casa Rinconada catches the summer solstice sunrise exactly — a shaft of light that enters the great kiva and strikes a specific niche in the opposite wall on the longest day of the year. On Fajada Butte, three stone slabs leaning against a cliff face create daggers of light that bisect spiral petroglyphs at every solstice, equinox, and major lunar standstill over an 18.6-year cycle. The great north road leaves Pueblo Alto and runs straight for fifty miles across the mesa, cutting through arroyos and climbing ridges with no apparent practical purpose — as if it wasn't built for walking, but for seeing.

These people were not primitive. They were doing what we're doing now: collecting data, watching patterns, building instruments to track cycles larger than a human lifetime.

SOLSTICE watches the same sky, over the same canyon, updated every twelve hours.

---

## Dashboard

**Live at [brooksgroves.com/solstice](https://brooksgroves.com/solstice/)**

[![SOLSTICE](assets/terrain.png)](https://brooksgroves.com/solstice/)

### What It Shows

**Terrain hero** — GPU-rendered 3D hillshade of Chaco Canyon from USGS 3DEP 1/3 arc-second LiDAR data, rendered via [forge3d](https://github.com/milos-agathon/forge3d). Camera positioned southeast of the canyon looking northwest, late-afternoon sun from WSW casting shadows into the mesa cuts. 4K resolution, clipped to the 14×11 km canyon core.

**Interactive map** — Leaflet on ESRI World Shaded Relief + OpenTopoMap overlay. Plots all ten major great houses with custom markers, the four Chacoan road spurs from Pueblo Alto, and two alignment lines projected from Casa Rinconada: the permanent summer solstice alignment at 66.5°, and today's actual sunrise azimuth updated from live data.

**Solar position** — Current sun azimuth, altitude, today's sunrise and sunset azimuths, above/below horizon status.

**Lunar position** — Moon azimuth, altitude, phase name, illumination percentage.

**Casa Rinconada alignment monitor** — Compares today's sunrise azimuth against the known 66.5° alignment of the great kiva's northeast doorway. Shows deviation in degrees. Activates when within 3.5° — around the summer solstice window each June.

**Solar event countdown** — Days to next vernal equinox, summer solstice, autumnal equinox, winter solstice.

**Great house cards** — All ten great houses in a scrollable strip. Period, room count, shape, alignment notes. Click any card to fly the map to that site.

---

## Great Houses of Chaco Canyon

| Site | Type | Rooms | Period | Notable |
|------|------|-------|--------|---------|
| Pueblo Bonito | Great House | ~800 | 850–1150 CE | Back wall faces true north within 0.1° |
| Chetro Ketl | Great House | ~500 | 1010–1110 CE | Unique colonnade; 4 stories |
| Pueblo del Arroyo | Great House | ~280 | 1075–1115 CE | Adjoins rare tri-walled structure |
| Casa Rinconada | Great Kiva | — | 1000–1100 CE | NE doorway aligns to summer solstice sunrise |
| Hungo Pavi | Great House | ~150 | 900–1100 CE | Largely unexcavated |
| Una Vida | Great House | ~160 | 850–1000 CE | One of the earliest; nearby petroglyphs |
| Penasco Blanco | Great House | ~150 | 900–1125 CE | Near possible 1054 CE supernova pictograph |
| Pueblo Alto | Great House | ~90 | 1020–1100 CE | Mesa-top terminus of the Great North Road |
| Kin Kletso | Great House | ~55 | 1125–1150 CE | Late McElmo-tradition construction |
| Wijiji | Great House | ~90 | 1100–1150 CE | Easternmost great house |

---

## The Alignment

Casa Rinconada is the largest isolated great kiva at Chaco — 19.5 meters in interior diameter, not attached to any residential great house, built to serve the broader canyon community. Its northeast doorway, documented by Anna Sofaer in 1979, aligns to a sunrise azimuth of approximately 66.5° — the sun's position on the horizon at summer solstice.

SOLSTICE tracks this alignment daily. The current deviation is displayed in the dashboard. As June 21 approaches, the gap closes. When today's sunrise azimuth falls within 3.5° of 66.5°, the alignment monitor activates.

---

## Terrain Rendering

The hero image is generated locally using [forge3d](https://github.com/milos-agathon/forge3d) — a Rust-built, WebGPU-accelerated 3D terrain renderer with a Python API. The workflow:

1. Download 1/3 arc-second DEM from [USGS 3DEP National Map](https://apps.nationalmap.gov/downloader/) — the `n37w108` tile covering 36°N–37°N
2. Clip to the canyon core (`data/chaco_canyon_clip.tif`, ~5 MB) using rasterio
3. Render at 4K via `forge3d.open_viewer_async()` with a southeast camera position and WSW late-afternoon sun
4. Commit `assets/terrain.png` to the repo

```python
with forge3d.open_viewer_async(terrain_path="data/chaco_canyon_clip.tif") as viewer:
    viewer.set_orbit_camera(phi_deg=210, theta_deg=28, radius=1.1)
    viewer.set_sun(azimuth_deg=240, elevation_deg=20)
    viewer.snapshot("assets/terrain.png", width=3840, height=2160)
```

The render is not automated (GPU rendering in GitHub Actions isn't practical). Run it locally once and commit the output.

---

## Setup

```bash
git clone https://github.com/bdgroves/solstice
cd solstice
pixi install

# Run astronomy fetch
pixi run fetch

# Render terrain (requires GPU, run locally once)
pip install forge3d
python render_terrain.py

git add assets/terrain.png
git commit -m "feat: terrain render"
git push
```

---

## Automation

GitHub Actions runs `fetch_solstice.py` at **06:00 and 18:00 UTC** daily, computing current sun/moon positions and upcoming solar events for Chaco Canyon using Python `ephem`, writing `data/solstice.json`, and committing the update.

The terrain render is committed as a static asset. Re-render anytime to try different camera angles or lighting.

---

## Data Sources

| Source | What |
|--------|------|
| [Python ephem](https://rhodesmill.org/pyhem/) | Sun/moon positions, solstice/equinox dates |
| [USGS 3DEP](https://apps.nationalmap.gov/downloader/) | 1/3 arc-second LiDAR DEM for terrain rendering |
| [forge3d](https://github.com/milos-agathon/forge3d) | GPU terrain renderer |
| [ESRI World Shaded Relief](https://server.arcgisonline.com/) | Map basemap tiles |
| [OpenTopoMap](https://opentopomap.org) | Topographic overlay |
| Sofaer, Sinclair & Doggett (1982) | Casa Rinconada alignment documentation |
| NPS / Chaco Research Archive | Great house coordinates and architecture |

---

## File Structure

```
solstice/
├── index.html                   # Dashboard — single file, no build step
├── fetch_solstice.py            # Astronomy → data/solstice.json
├── render_terrain.py            # forge3d terrain render (run locally)
├── pixi.toml                    # Python environment
├── data/
│   ├── solstice.json            # Generated by fetch script
│   ├── great_houses.geojson     # Site coordinates and attributes
│   └── roads.geojson            # Chacoan road network
├── assets/
│   └── terrain.png              # forge3d 4K terrain render
└── .github/workflows/
    └── update-solstice.yml      # Twice-daily automation
```

---

## Part of the Ology Hub

SOLSTICE is the archaeology section of [brooksgroves.com](https://brooksgroves.com).

| Dashboard | Domain | Stack |
|-----------|--------|-------|
| [PELE](https://brooksgroves.com/geology) | Volcanology — Kīlauea eruption tracker | Python · USGS Volcanoes API · Leaflet |
| [AFTERSHOCK](https://brooksgroves.com/seismology) | Seismology — USGS earthquake monitor | Python · USGS Earthquake API · Leaflet |
| [SOLSTICE](https://brooksgroves.com/solstice) | Archaeology — Chaco Canyon archaeoastronomy | Python · ephem · forge3d · Leaflet |
| [RIDGELINE](https://bdgroves.github.io/ridgeline) | WUI search and rescue call volume | Python · Phoenix Fire Dept · Folium |
| [EDGAR](https://bdgroves.github.io/EDGAR) | MLB analytics — Mariners & Rainiers | Python · pybaseball · mlb-statsapi |
| [Sierra Streamflow](https://bdgroves.github.io/sierra-streamflow) | USGS streamflow — Sierra Nevada | Python · USGS NWIS · Leaflet |

---

*Built by [Brooks Groves](https://brooksgroves.com) · April 2026*
