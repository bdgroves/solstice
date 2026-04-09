# SOLSTICE

**Chaco Canyon — Archaeoastronomy & Remote Sensing Dashboard**

---

In the summer of A.D. 1054, a supernova blazed in the sky bright enough to cast shadows at night. Somewhere near Fajada Butte, a Chacoan astronomer watched it rise, and some scholars believe they marked it on the canyon wall. We don't know their name. We don't know their language. We don't know what they called the sky. But we know they were watching — carefully, systematically, over generations — because they built it into the stone.

The great houses of Chaco Canyon are not houses in any ordinary sense. Pueblo Bonito's rear wall runs true north to within a tenth of a degree. Casa Rinconada's northeast doorway catches the summer solstice sunrise exactly. The Sun Dagger site on Fajada Butte — three stone slabs leaning against a cliff face — marks the solstices, equinoxes, and even the 18.6-year lunar standstill cycle with daggers of light bisecting spiral petroglyphs. The road network radiating from the canyon runs perfectly straight for fifty miles, cutting across arroyos and mesas with no apparent practical purpose, as if it wasn't for walking but for seeing.

SOLSTICE tracks the sky above Chaco Canyon in real time and maps what the Ancestral Puebloans built to watch it.

---

## What It Does

- **Live astronomical readouts** for Chaco Canyon — current sun and moon azimuth, altitude, lunar phase, today's sunrise azimuth
- **Alignment monitor** for Casa Rinconada's northeast doorway (active near summer solstice when sunrise azimuth ≈ 66.5°)
- **Solar event countdown** — days to next solstice, equinox
- **Interactive Leaflet map** with all ten major great houses, the Chacoan road network, and live solar alignment lines projected from Casa Rinconada
- **forge3d terrain render** — GPU-accelerated 3D hillshade of Chaco Canyon from USGS 3DEP 1/3 arc-second LiDAR data (see below)
- **Great house cards** — period, room count, alignment notes, construction details
- Automated nightly updates via GitHub Actions

---

## Dashboard

Live at **[brooksgroves.com/archaeology](https://brooksgroves.com/archaeology)** · GitHub Pages: `bdgroves.github.io/SOLSTICE`

---

## Terrain Rendering with forge3d

SOLSTICE uses [forge3d](https://github.com/milos-agathon/forge3d) — a GPU-accelerated 3D terrain renderer built in Rust with WebGPU, exposed to Python — to generate the canyon hero render from real USGS 3DEP elevation data.

```bash
# Install forge3d (not in default pixi env — run locally, needs GPU)
pip install forge3d

# Download DEM + render terrain (run once, commit the output)
pixi run render
```

The render script downloads a 1/3 arc-second DEM for the Chaco Canyon bounding box from the USGS National Map, then renders a dramatic 3D view with late-afternoon sun casting shadows into the canyon. Output goes to `assets/terrain.png` — commit it and it appears in the dashboard header.

You can re-render any time to try different camera angles or lighting:

```python
# In render_terrain.py — tweak these for different perspectives
viewer.set_orbit_camera(phi_deg=210, theta_deg=28, radius=1.1)
viewer.set_sun(azimuth_deg=240, elevation_deg=22)
```

---

## Setup

```bash
# Clone and set up environment
git clone https://github.com/bdgroves/SOLSTICE
cd SOLSTICE
pixi install

# Run the astronomy fetch (writes data/solstice.json)
pixi run fetch

# Optional: render terrain (requires GPU, run locally)
pip install forge3d
pixi run render

# Open dashboard
open index.html
```

---

## Data Sources

| Source | What |
|--------|------|
| [Python ephem](https://rhodesmill.org/pyhem/) | Sun/moon positions, solstice/equinox dates |
| [USGS 3DEP](https://apps.nationalmap.gov/downloader/) | 1/3 arc-second DEM for terrain rendering |
| [ESRI World Shaded Relief](https://server.arcgisonline.com/) | Basemap tiles |
| [OpenTopoMap](https://opentopomap.org) | Topographic overlay |
| Sofaer et al. (1979, 2017) | Casa Rinconada alignment documentation |
| NPS / Chaco Research Archive | Great house coordinates and architecture |

---

## Great Houses

| Site | Type | Rooms | Period |
|------|------|-------|--------|
| Pueblo Bonito | Great House | ~800 | 850–1150 CE |
| Chetro Ketl | Great House | ~500 | 1010–1110 CE |
| Pueblo del Arroyo | Great House | ~280 | 1075–1115 CE |
| Casa Rinconada | Great Kiva | — | 1000–1100 CE |
| Hungo Pavi | Great House | ~150 | 900–1100 CE |
| Una Vida | Great House | ~160 | 850–1000 CE |
| Penasco Blanco | Great House | ~150 | 900–1125 CE |
| Pueblo Alto | Great House | ~90 | 1020–1100 CE |
| Kin Kletso | Great House | ~55 | 1125–1150 CE |
| Wijiji | Great House | ~90 | 1100–1150 CE |

---

## Automation

GitHub Actions runs `fetch_solstice.py` twice daily (06:00 and 18:00 UTC), updating `data/solstice.json` and pushing the commit. The terrain render is **not** automated — GPU rendering isn't practical in Actions. Run it locally once, commit `assets/terrain.png`, and it stays.

---

## File Structure

```
SOLSTICE/
├── index.html              # Dashboard (single-file, no build)
├── fetch_solstice.py       # Astronomy calculations → data/solstice.json
├── render_terrain.py       # forge3d terrain render (run locally once)
├── pixi.toml
├── data/
│   ├── solstice.json       # Generated — do not edit manually
│   ├── great_houses.geojson
│   └── roads.geojson
├── assets/
│   └── terrain.png         # Generated by render_terrain.py, commit this
└── .github/workflows/
    └── update-solstice.yml
```

---

## Related Projects

- **[PELE](https://brooksgroves.com/geology)** — Kīlauea eruption tracker
- **[AFTERSHOCK](https://brooksgroves.com/seismology)** — USGS earthquake monitor
- **[RIDGELINE](https://bdgroves.github.io/ridgeline)** — WUI search and rescue analysis
- **[Project Kiva](https://github.com/bdgroves/project-kiva)** — Southwest archaeology LiDAR pipeline (R/rayshader)

---

*"The canyon aligned its stones to the sky. We aligned our data to the canyon."*
