# Handoff: SOLSTICE (as of Oct 6, 2026)

For a new chat picking this up. Read this, then `git pull --rebase` (a daily Action commits `data/sky.json`).

Live: [brooksgroves.com/solstice](https://brooksgroves.com/solstice/) · blog: `bdgroves.github.io/blog/solstice-revisited-post.html` (has its own copies of the renders in `blog/img/solstice-revisited/`; refresh them when the renders change).

## Data branches (kept off main)

| Branch | Made by | What |
|---|---|---|
| `prep-data` | Prep render data (`tools/prep_data.py`) | 5 m and 30 m 3DEP DEMs, 2.5 m NAIP mosaic |
| `prep-lidar` | Prep lidar (`tools/prep_lidar.py`) | 89 × 0.5 m ground tiles from the CO_CONMGaps_D24 point clouds, within 1.5 km of a site; `index.json`, `lidar.log` |
| `renders-hd` | Export HD flyover (input: render run id) | 1080p flyover for the "Watch in HD" link |
| `render-logs` | Render one flyover chunk (debug) | the last debug chunk's log |

Fetch them with `git fetch origin <branch> && git archive FETCH_HEAD | tar -x -C prep` (lidar goes in `prep/lidar`).

## Why the renders are built the way they are

- **forge3d's viewer caps a terrain at ~2048 vertices across** and point-samples anything bigger (tested with synthetic DEMs). The whole canyon is ~22 km, so ~11 m whatever the source.
- **`tools/layers.py` draws each frame in layers** (far / mid / near, plus a `detail` tier for the Bonito close-up), each under the cap, same camera and Sun; the finer layer wins wherever it has terrain (`composite()` via the sky mask). Windows reach toward the Sun only as far as terrain can actually shade them (`shadow_reach`).
- **USGS's finished 1 m DEM has a hole over the canyon core** (around Pueblo Bonito) filled with coarse data. The 0.5 m CONMGaps tiles are patched into every window and into the 5 m DEM (`patch_lidar`, 40 m feather; they agree with 3DEP to a few cm).
- **The 3DEP ImageServer 502s on some windows over the core.** `window_layer` retries with smaller requests, then falls back to the (lidar-patched) 5 m DEM.
- **20 parallel jobs get turned away by USGS / Planetary Computer.** NAIP fetches retry; each chunk retries up to 3 times (finished frames are kept).

## Running renders (GitHub Actions; the PC can't: Windows Application Control blocks new DLLs)

- Full: **Render with forge3d** (`render.yml`), chunks=30, width=1920. ~90 min. Commits stills + flyover to `assets/`.
- **Only some chunks:** inputs `only=2,6,8` and `reuse_run=<run id>`; publish pulls the other chunks' `frames-*` artifacts from that run (artifacts last 3 days).
- **A chunk keeps failing:** run **Render one flyover chunk (debug)** with `chunk=N frames=3`, then read `git show origin/render-logs:render.log`. Job logs/artifacts can't be downloaded from a cloud session; the workflows commit logs instead.
- After a render: run **Export HD flyover** with the run id, and refresh the blog copies.
- Workflow dispatch from a session: `gh api -X POST repos/bdgroves/solstice/actions/workflows/<file>/dispatches -f ref=main -f 'inputs[...]=...'` (`gh workflow run` needs GraphQL, which is blocked).
- Local test without network: `python tools/render.py stills --source prep --only bonito-close --width 960` (needs Mesa Vulkan + Xvfb; works in the cloud container).

## Skylines

`tools/horizon.py` → `data/horizons.json` (8 sites have skylines; 11 have notes). Uses the lidar at 1 m from **80 m** to 2.4 km of each site (closer than 80 m the bare-earth lidar sees the ruin's own rubble mound as a horizon), then 5 m, then 30 m. Then `pixi run fetch` (or `python fetch_solstice.py`) rebuilds `data/sky.json`. The Oct 6 lidar skylines moved Pueblo Bonito's June sunrise 07:06 → 07:11 and Una Vida's 07:34 → 07:43.

## Notebook

`notebooks/solstice.ipynb` walks the pipeline with the repo's own functions (it imports `tools/render.py`, `tools/layers.py` and `fetch_solstice.py`, so keep their names stable or update it). Parameters cell: `WIDTH`, `SOURCE`, `RENDER`. Committed with outputs; **Run notebooks** (`notebooks.yml`) re-executes it with papermill and commits it back. A full run takes 2–3 min on a CPU, most of it the four-layer render.

## Ideas not done

- A close-up of Casa Rinconada or Chetro Ketl (one more entry in `stills()` with a `TIERS` override).
- The flyover's near layers are 2.5–2.75 m because the June dawn Sun is ~2° up and shadows reach ~3 km; starting the flight later would sharpen it.
- `prep-lidar` only covers 1.5 km around sites (the full survey is 490 tiles, 26.6 GB, and the S3 mirror 404s, so downloads come from rockyweb at ~1 tile/min).

## Conventions

Friendly, warm voice on the blog. Every number gets checked by a separate agent before publishing. Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` plus the session link. Chaco Canyon is the ancestral homeland of the Pueblo peoples and the Diné; keep that line on the page and in posts.
