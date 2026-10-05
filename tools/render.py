"""
SOLSTICE renders: Chaco Canyon in forge3d, lit by the real Sun.

    python tools/render.py stills  --prep prep --out renders
    python tools/render.py flyover --prep prep --out frames --chunk 3 --chunks 24
    python tools/render.py encode  --frames frames --out renders/chaco-flyover.mp4
    python tools/render.py today   --prep prep --out renders     # today's sunrise

Terrain: USGS 3DEP lidar, 1 m near the camera (nested layers, see layers.py). Colour: USGS NAIP 2022 aerial photography.
The Sun is placed with forge3d's solar calculator for the date and time named
in each shot. Runs headless on a CPU (Mesa llvmpipe + Xvfb) or on a GPU.

Viewer world coordinates (from the forge3d source): x = UTM easting,
y = elevation - DEM minimum, z = -UTM northing.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFont
from rasterio.warp import transform

Image.MAX_IMAGE_PIXELS = None
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FONTS = HERE / "fonts"


class _Mountain(tzinfo):
    """US Mountain Time with daylight saving (2007 rules), for Pythons with no
    time-zone database (Windows without the tzdata package)."""
    def _dst(self, dt):
        y = dt.year
        mar = date(y, 3, 8) + timedelta(days=(6 - date(y, 3, 8).weekday()) % 7)    # 2nd Sunday
        nov = date(y, 11, 1) + timedelta(days=(6 - date(y, 11, 1).weekday()) % 7)  # 1st Sunday
        n = dt.replace(tzinfo=None)
        return datetime(mar.year, mar.month, mar.day, 2) <= n < datetime(nov.year, nov.month, nov.day, 1)

    def utcoffset(self, dt):
        return timedelta(hours=-6 if self._dst(dt) else -7)

    def dst(self, dt):
        return timedelta(hours=1 if self._dst(dt) else 0)

    def tzname(self, dt):
        return "MDT" if self._dst(dt) else "MST"

    def fromutc(self, dt):
        st = dt + timedelta(hours=-7)
        return (dt + timedelta(hours=-6) if self._dst(st) else st).replace(tzinfo=self)


try:
    TZ = ZoneInfo("America/Denver")
except Exception:
    TZ = _Mountain()
LAT0, LON0 = 36.0606, -107.9616          # Pueblo Bonito: reference point for the Sun
CRS = "EPSG:26913"


# ── scene ─────────────────────────────────────────────────────────────────────
class Scene:
    def __init__(self, prep: Path, work: Path, source: str = "prep"):
        self.crs = CRS
        self.source = source
        self.work = work
        self.dem_src = prep / "chaco_dem_5m.tif"
        with rasterio.open(self.dem_src) as s:
            self.dem = s.read(1)          # 5 m: ground heights, camera floors
            self.T = s.transform
            self.B = s.bounds
        self.min_h = float(np.nanmin(self.dem))
        self.naip_src = prep / "chaco_naip_2p5m.tif"
        with rasterio.open(self.naip_src) as s:
            self.naip_bounds = s.bounds
        self.texture = work / "naip_8k_graded.png"
        if not self.texture.exists():
            grade_texture(self.naip_src, self.texture)
        self.sites = {s["id"]: s for s in json.loads((ROOT / "data/sites.json").read_text())["sites"]}
        from layers import far_layer
        self.far = far_layer(self, work)

    def utm(self, lon, lat):
        (x,), (y,) = transform("EPSG:4326", CRS, [lon], [lat])
        return x, y

    def ground(self, x, y):
        r, c = rasterio.transform.rowcol(self.T, x, y)
        r = min(max(r, 0), self.dem.shape[0] - 1)
        c = min(max(c, 0), self.dem.shape[1] - 1)
        return float(self.dem[r, c])

    def ground_max(self, x, y, radius=120.0):
        r, c = rasterio.transform.rowcol(self.T, x, y)
        k = int(radius / 5)
        win = self.dem[max(r - k, 0):r + k + 1, max(c - k, 0):c + k + 1]
        return float(np.nanmax(win)) if win.size else self.ground(x, y)

    def world(self, x, y, h):
        return np.array([x, h - self.min_h, -y], dtype=float)

    def site_world(self, sid, lift=0.0):
        s = self.sites[sid]
        x, y = self.utm(s["lon"], s["lat"])
        return self.world(x, y, self.ground(x, y) + lift)


def grade_texture(src_tif: Path, out_png: Path):
    """NAIP -> 8K texture, saturation x1.25 and a gentle S-curve (after the
    forge3d Bryce example: offsets the renderer's desaturating sky tint)."""
    with rasterio.open(src_tif) as s:
        a = np.moveaxis(s.read(), 0, -1)
    im = Image.fromarray(a)
    w, h = im.size
    k = 8192 / max(w, h)
    im = im.resize((int(w * k), int(h * k)), Image.LANCZOS)
    a = np.asarray(im)
    out = np.empty_like(a)
    wl = np.array([0.2126, 0.7152, 0.0722], np.float32)
    for r0 in range(0, a.shape[0], 512):
        rgb = a[r0:r0 + 512].astype(np.float32) / 255
        lum = (rgb @ wl)[..., None]
        rgb = np.clip(lum + (rgb - lum) * 1.25, 0, 1)
        rgb += 0.10 * (rgb - 0.5) * (1 - np.abs(2 * rgb - 1))
        out[r0:r0 + 512] = (np.clip(rgb * 0.92, 0, 1) * 255 + 0.5).astype(np.uint8)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out).save(out_png)


def sun_at(local: datetime):
    import forge3d as f3d
    u = local.astimezone(timezone.utc)
    s = f3d.sun_position_utc(LAT0, LON0, u.year, u.month, u.day, u.hour, u.minute, u.second)
    return float(s.azimuth), float(s.elevation)


MAX_THETA = 85.0   # the viewer won't look flatter than 5 deg below horizontal


def effective_eye(eye, aim):
    """Where the viewer actually puts the camera: same target, radius and
    heading, with the polar angle clamped to 85 deg (forge3d viewer behaviour,
    measured with marker renders)."""
    off = eye - aim
    r = float(np.linalg.norm(off))
    theta = math.degrees(math.acos(off[1] / r))
    if theta <= MAX_THETA:
        return eye
    phi = math.atan2(off[2], off[0])
    t = math.radians(MAX_THETA)
    return aim + np.array([r * math.sin(t) * math.cos(phi), r * math.cos(t), r * math.sin(t) * math.sin(phi)])


def camera_cmd(eye, aim, fov):
    off = eye - aim
    r = float(np.linalg.norm(off))
    return {"cmd": "set_terrain_camera", "phi_deg": math.degrees(math.atan2(off[2], off[0])),
            "theta_deg": min(MAX_THETA, math.degrees(math.acos(off[1] / r))), "radius": r, "fov_deg": fov,
            "target": [float(v) for v in aim]}


def project(p, eye, aim, fov, size):
    eye = effective_eye(eye, aim)
    f = aim - eye
    f /= np.linalg.norm(f)
    r = np.cross(f, [0.0, 1.0, 0.0])
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    d = p - eye
    z = float(d @ f)
    if z <= 1:
        return None
    th = math.tan(math.radians(fov) / 2)
    x = (d @ r) / (z * th * size[0] / size[1])
    y = (d @ u) / (z * th)
    return (x + 1) / 2 * size[0], (1 - y) / 2 * size[1], z


class Viewer:
    """One forge3d viewer holding one terrain layer (see layers.py)."""
    def __init__(self, scene: Scene, layer, size, fov):
        from forge3d.viewer import open_viewer_async
        self.size = size
        # the viewer puts y = 0 at its own DEM's minimum; cameras are in the scene's frame
        self.dy = layer.min_h - scene.min_h
        self.v = open_viewer_async(width=size[0], height=size[1], terrain_path=str(layer.dem),
                                   fov_deg=fov, timeout=600)
        self.v.load_overlay("naip", str(layer.texture), extent=layer.ext, z_order=0)
        self.v.send_ipc({"cmd": "set_terrain_pbr", "enabled": True, "exposure": 0.42, "shadow_map_res": 4096,
                         "height_ao": {"enabled": True, "strength": 0.8, "max_distance": 150.0},
                         "sun_visibility": {"enabled": True, "mode": "soft", "max_distance": 4000.0}})
        self.v.send_ipc({"cmd": "set_terrain", "ambient": 0.07, "zscale": 1.0})

    def sun(self, az, el):
        self.v.send_ipc({"cmd": "set_terrain_sun", "azimuth_deg": az, "elevation_deg": el, "intensity": 1.0})

    def shot(self, eye, aim, fov, path):
        d = np.array([0.0, self.dy, 0.0])
        self.v.send_ipc(camera_cmd(eye - d, aim - d, fov))
        self.v.snapshot(str(path), *self.size)

    def close(self):
        self.v.close()


class Stack:
    """Viewers for a far/mid/near layer stack, rendered and composited together."""
    def __init__(self, scene: Scene, layers, size, fov):
        self.layers = layers
        self.vs = []
        try:
            for L in layers:
                self.vs.append(Viewer(scene, L, size, fov))
        except Exception:
            self.close()
            raise

    def sun(self, az, el):
        for v in self.vs:
            v.sun(az, el)

    def shot(self, eye, aim, fov, path: Path):
        from layers import composite
        raws = []
        for L, v in zip(self.layers, self.vs):
            p = path.with_name(f"{path.stem}.{L.name}.png")
            v.shot(eye, aim, fov, p)
            raws.append(p)
        img = composite(raws)
        Image.fromarray((np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)).save(path)
        for p in raws:
            p.unlink()

    def close(self):
        for v in self.vs:
            v.close()


def stack_for(scene: Scene, cams, fov, size, sun, tag=""):
    from layers import layers_for
    return Stack(scene, layers_for(scene, cams, fov, size[0] / size[1], sun, scene.work, scene.source, tag),
                 size, fov)


# ── look ──────────────────────────────────────────────────────────────────────
@dataclass
class Sky:
    zenith: tuple
    horizon: tuple
    warm: tuple = (1.06, 1.0, 0.90)
    haze: float = 0.5


DAWN = Sky((0.16, 0.25, 0.46), (0.98, 0.79, 0.60))
WINTER = Sky((0.20, 0.32, 0.55), (0.95, 0.82, 0.70), (1.04, 1.0, 0.93))
DAY = Sky((0.25, 0.45, 0.75), (0.80, 0.86, 0.92), (1.0, 1.0, 0.98), 0.35)


def finish(png: Path, sky: Sky) -> Image.Image:
    """Paint the sky, add distance haze along the horizon, warm the light."""
    img = np.asarray(Image.open(png).convert("RGB")).astype(np.float32) / 255
    mask = sky_mask(img)
    rows = np.flatnonzero(~mask.all(axis=1))
    hz = int(rows[0]) if rows.size else img.shape[0] // 2
    H, W = img.shape[:2]
    yy = np.arange(H, dtype=np.float32)[:, None]
    t = np.clip(yy / max(hz, 1), 0, 1) ** 1.5
    zen, hor = np.array(sky.zenith), np.array(sky.horizon)
    skyimg = np.broadcast_to((zen * (1 - t) + hor * t)[:, None, :], (H, W, 3))
    ter = np.clip((img - 0.5) * 1.12 + 0.5, 0, 1) * np.array(sky.warm)
    k = (np.clip(1 - (yy - hz) / (H * 0.38), 0, 1) ** 2.4 * sky.haze)[..., None]
    ter = ter * (1 - k) + hor * k
    out = np.where(mask[..., None], skyimg, ter)
    return Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8))


def sky_mask(img: np.ndarray) -> np.ndarray:
    """The viewer's background is a smooth, pale gradient; terrain is textured
    and darker. Walk down each column until that stops being true."""
    lum = img @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    H, W = lum.shape
    grad = np.abs(np.diff(lum, axis=0, prepend=lum[:1]))
    not_sky = (lum < 0.86) | (grad > 0.02)
    first = np.where(not_sky.any(axis=0), not_sky.argmax(axis=0), H)
    # smooth the silhouette a little, never letting it rise above the data
    return np.arange(H)[:, None] < first[None, :]


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def place_labels(items, size):
    """items: [(x, y, text, sub, alpha)] -> stem lengths that keep text boxes apart."""
    s = size[0] / 1920
    placed, out = [], []
    for x, y, text, sub, a in sorted(items, key=lambda t: -t[1]):
        w = (len(text) * 19 + 20) * s
        stem = 46 * s
        for _ in range(12):
            box = (x, y - stem - 34 * s, x + w, y - stem + (34 if sub else 6) * s)
            if not any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in placed):
                break
            stem += 30 * s
        placed.append(box)
        out.append((x, y, text, sub, a, stem / s))
    return out


def label(im: Image.Image, xy, text, sub=None, alpha=1.0, scale=1.0, stem_px=46):
    """A pin and a two-line label, after the page's typography."""
    if xy is None or alpha <= 0.01:
        return
    x, y = xy
    W, H = im.size
    if not (0 < x < W and 0 < y < H):
        return
    s = scale * W / 1920
    # Fade pins that are sliding off the frame instead of cutting them in half.
    edge = min(x, W - x) / (0.05 * W), (H - y) / (0.07 * H)
    alpha *= max(0.0, min(1.0, *edge))
    if alpha <= 0.01:
        return
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a = int(255 * alpha)
    stem = stem_px * s
    d.line([(x, y), (x, y - stem)], fill=(255, 244, 220, a), width=max(1, int(2 * s)))
    d.ellipse([x - 4 * s, y - 4 * s, x + 4 * s, y + 4 * s], fill=(255, 220, 160, a))
    f1 = font("cinzel-latin-600-normal.woff", int(30 * s))
    tx, ty = x + 10 * s, y - stem - 30 * s
    f2 = font("crimson-text-latin-400-italic.woff", int(22 * s))
    tw = max(d.textlength(text, font=f1), d.textlength(sub, font=f2) if sub else 0)
    if tx + tw > W - 12 * s:            # near the right edge: put the text left of the pin
        tx = x - 10 * s - tw
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
        d.text((tx + dx * s, ty + dy * s), text, font=f1, fill=(20, 14, 8, int(a * 0.55)))
    d.text((tx, ty), text, font=f1, fill=(255, 246, 228, a))
    if sub:
        d.text((tx + 1, ty + 34 * s), sub, font=f2, fill=(20, 14, 8, int(a * 0.5)))
        d.text((tx, ty + 33 * s), sub, font=f2, fill=(250, 232, 200, a))
    im.paste(Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB"))


def caption(im: Image.Image, title, sub=None, alpha=1.0, where="bottom"):
    if alpha <= 0.01:
        return
    W, H = im.size
    s = W / 1920
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a = int(255 * alpha)
    f1 = font("cinzel-latin-400-normal.woff", int(44 * s))
    f2 = font("courier-prime-latin-400-normal.woff", int(20 * s))
    y = H - 150 * s if where == "bottom" else 70 * s
    grad = np.linspace(0, 0.55, int(220 * s)) if where == "bottom" else np.linspace(0.45, 0, int(200 * s))
    for i, g in enumerate(grad):
        yy = H - len(grad) + i if where == "bottom" else i
        d.line([(0, yy), (W, yy)], fill=(10, 7, 5, int(g * a)))
    d.text((70 * s, y), title, font=f1, fill=(240, 214, 160, a))
    if sub:
        d.text((72 * s, y + 60 * s), sub, font=f2, fill=(220, 200, 170, a))
    im.paste(Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB"))


# ── stills ────────────────────────────────────────────────────────────────────
def solstice_dates(year):
    try:
        import ephem
    except ImportError:
        return _solstices_meeus(year)
    loc = lambda d: ephem.Date(d).datetime().replace(tzinfo=timezone.utc).astimezone(TZ).date()
    return loc(ephem.next_summer_solstice(f"{year}/1/1")), loc(ephem.next_winter_solstice(f"{year}/1/1"))


def _solstices_meeus(year):
    """Solstice dates from Meeus, Astronomical Algorithms, table 27.B (good to
    well under an hour, plenty to name the local date)."""
    Y = (year - 2000) / 1000
    jun = 2451716.56767 + 365241.62603 * Y + 0.00325 * Y**2 + 0.00888 * Y**3 - 0.00030 * Y**4
    dec = 2451900.05952 + 365242.74049 * Y - 0.06223 * Y**2 - 0.00823 * Y**3 + 0.00032 * Y**4
    loc = lambda jd: (datetime(2000, 1, 1, 12, tzinfo=timezone.utc) + timedelta(days=jd - 2451545.0)).astimezone(TZ).date()
    return loc(jun), loc(dec)


def _sunrise_f3d(day: date):
    """Flat-horizon sunrise (upper limb, standard refraction: -0.833 deg) from
    forge3d's solar calculator, when PyEphem isn't installed."""
    import forge3d as f3d
    el = lambda t: float(f3d.sun_position_utc(LAT0, LON0, t.year, t.month, t.day, t.hour, t.minute, t.second).elevation)
    lo = datetime(day.year, day.month, day.day, 3, tzinfo=TZ).astimezone(timezone.utc)
    hi = datetime(day.year, day.month, day.day, 11, tzinfo=TZ).astimezone(timezone.utc)
    for _ in range(40):
        mid = lo + (hi - lo) / 2
        if el(mid) < -0.833:
            lo = mid
        else:
            hi = mid
    return hi.replace(microsecond=0)


def sunrise_plus(day: date, minutes: float, sc: Scene):
    """Local time when the Sun is `minutes` past sunrise on a flat horizon."""
    try:
        import ephem
    except ImportError:
        return (_sunrise_f3d(day) + timedelta(minutes=minutes)).astimezone(TZ)
    o = ephem.Observer()
    o.lat, o.lon, o.elevation = str(LAT0), str(LON0), 1870
    o.date = ephem.Date(datetime(day.year, day.month, day.day, 3, tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None))
    r = o.next_rising(ephem.Sun()).datetime().replace(tzinfo=timezone.utc)
    return (r + timedelta(minutes=minutes)).astimezone(TZ)


def stills(sc: Scene, out: Path, work: Path, size=(1920, 1080), only=None):
    year = 2027
    ss, ws = solstice_dates(year)
    fx, fy = sc.utm(sc.sites["fajada-butte"]["lon"], sc.sites["fajada-butte"]["lat"])
    bx, by = sc.utm(sc.sites["pueblo-bonito"]["lon"], sc.sites["pueblo-bonito"]["lat"])
    # One camera, two dawns: the canyon from above Chacra Mesa, looking up-canyon past Fajada Butte.
    wide_eye = sc.world(fx + 2600, fy - 2300, 2330)
    wide_aim = sc.world(bx + 1200, by - 600, 1900)
    shots = [
        ("summer-dawn", ss, 24, wide_eye, wide_aim, 46, DAWN,
         ["fajada-butte", "una-vida", "pueblo-bonito", "hungo-pavi"]),
        ("winter-dawn", ws, 30, wide_eye, wide_aim, 46, WINTER,
         ["fajada-butte", "una-vida", "pueblo-bonito", "hungo-pavi"]),
    ]
    ax_, ay_ = sc.utm(sc.sites["pueblo-alto"]["lon"], sc.sites["pueblo-alto"]["lat"])
    bon_eye = sc.world(ax_ + 700, ay_ + 1100, 2260)
    bon_aim = sc.site_world("casa-rinconada", 0)
    shots.append(("bonito-dawn", ss, 26, bon_eye, bon_aim, 38, DAWN,
                  ["pueblo-bonito", "chetro-ketl", "casa-rinconada", "pueblo-alto"]))
    meta = []
    if only:
        shots = [s for s in shots if s[0] in only]
    for name, day, mins, eye, aim, fov, sky, labels in shots:
        when = sunrise_plus(day, mins, sc)
        az, el = sun_at(when)
        raw = work / f"{name}.png"
        t = time.time()
        v = stack_for(sc, [(eye, aim)], fov, size, (az, el), tag=f"-{name}")
        try:
            v.sun(az, el)
            v.shot(eye, aim, fov, raw)
        finally:
            v.close()
        im = finish(raw, sky)
        items = []
        for sid in labels:
            p = project(sc.site_world(sid), eye, aim, fov, size)
            if p:
                items.append((p[0], p[1], sc.sites[sid]["name"], None, 1.0))
        for x, y, text, sub, a, stem in place_labels(items, size):
            label(im, (x, y), text, sub, a, stem_px=stem)
        caption(im, f"Chaco Canyon · {'summer' if day == ss else 'winter'} solstice sunrise",
                f"{when.strftime('%B')} {when.day}, {when.strftime('%I:%M %p').lstrip('0')} MST · sun {el:.0f}° above the horizon at {az:.0f}°"
                .replace(" MST", " MDT" if when.dst() else " MST"))
        im.save(out / f"{name}.jpg", quality=88)
        meta.append({"file": f"{name}.jpg", "local_time": when.isoformat(timespec="minutes"),
                     "sun_azimuth": round(az, 1), "sun_elevation": round(el, 1)})
        print(f"{name}: {time.time() - t:.0f}s  sun {az:.1f}/{el:.1f}", flush=True)
    (out / "stills.json").write_text(json.dumps(meta, indent=1))


# ── flyover ───────────────────────────────────────────────────────────────────
FPS = 30
FLY_DAY = date(2027, 6, 21)
FLY_MINUTES = 16                    # minutes after sunrise at the first frame


def flight(sc: Scene):
    """Keyframes: (time s, eye x, y, alt m, aim site or (x, y, h), label sites)."""
    S = sc.sites
    def p(sid, de=0.0, dn=0.0):
        x, y = sc.utm(S[sid]["lon"], S[sid]["lat"])
        return x + de, y + dn
    fx, fy = p("fajada-butte")
    keys = [
        # t,   eye (x, y),                      alt,  aim (site, height above ground)
        (0.0,  p("fajada-butte", 2300, -900),   2210, ("site", "fajada-butte", 0)),
        (6.0,  p("fajada-butte", 1300, 1300),   2190, ("site", "fajada-butte", 0)),
        (11.0, p("fajada-butte", -100, 900),    2210, ("site", "hungo-pavi", 0)),
        (16.0, p("una-vida", -350, 450),        2190, ("site", "chetro-ketl", 0)),
        (22.0, p("hungo-pavi", -450, 150),      2170, ("site", "chetro-ketl", 0)),
        (28.0, p("chetro-ketl", 450, -550),     2150, ("site", "pueblo-bonito", 0)),
        (33.0, p("pueblo-bonito", 200, -650),   2130, ("site", "pueblo-bonito", 0)),
        (38.0, p("pueblo-bonito", -700, -350),  2190, ("site", "kin-kletso", 0)),
        (44.0, p("kin-kletso", -1400, 500),     2230, ("site", "penasco-blanco", 0)),
        (50.0, p("penasco-blanco", 1300, 100),  2190, ("site", "penasco-blanco", 0)),
        (55.0, p("penasco-blanco", 350, 650),   2200, ("point", p("penasco-blanco", -2600, -1800), 1880)),
    ]
    return keys


def catmull(pts, ts, t):
    i = max(0, min(len(ts) - 2, int(np.searchsorted(ts, t, side="right") - 1)))
    p0, p1, p2, p3 = pts[max(i - 1, 0)], pts[i], pts[i + 1], pts[min(i + 2, len(pts) - 1)]
    u = (t - ts[i]) / (ts[i + 1] - ts[i])
    return 0.5 * (2 * p1 + (-p0 + p2) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u ** 2
                  + (-p0 + 3 * p1 - 3 * p2 + p3) * u ** 3)


def camera_path(sc: Scene):
    keys = flight(sc)
    ts = np.array([k[0] for k in keys])
    eyes, aims = [], []
    for t, (x, y), alt, aim in keys:
        eyes.append(np.array([x, y, alt]))
        if aim[0] == "site":
            ax, ay = sc.utm(sc.sites[aim[1]]["lon"], sc.sites[aim[1]]["lat"])
            aims.append(np.array([ax, ay, sc.ground(ax, ay) + aim[2]]))
        else:
            (ax, ay), ah = aim[1], aim[2]
            aims.append(np.array([ax, ay, ah]))
    eyes, aims = np.array(eyes), np.array(aims)
    n = int(ts[-1] * FPS) + 1
    E = np.array([catmull(eyes, ts, i / FPS) for i in range(n)])
    A = np.array([catmull(aims, ts, i / FPS) for i in range(n)])
    # stay at least 90 m above the highest ground nearby, then smooth
    floor = np.array([sc.ground_max(x, y, 150) + 90 for x, y, _ in E])
    E[:, 2] = np.maximum(E[:, 2], floor)
    k = np.exp(-0.5 * (np.arange(-30, 31) / 10.0) ** 2)
    k /= k.sum()
    for c in range(3):
        E[:, c] = np.convolve(np.pad(E[:, c], 30, mode="edge"), k, mode="valid")
        A[:, c] = np.convolve(np.pad(A[:, c], 30, mode="edge"), k, mode="valid")
    E[:, 2] = np.maximum(E[:, 2], floor)
    return [(sc.world(*e), sc.world(*a)) for e, a in zip(E, A)]


LABELS = [  # site, sub-label, (start s, end s)
    ("fajada-butte", "the Sun Dagger", (0.5, 12.0)),
    ("una-vida", "begun in the 800s", (9.0, 18.0)),
    ("hungo-pavi", "never excavated", (15.0, 24.0)),
    ("chetro-ketl", "the colonnade", (21.0, 30.0)),
    ("pueblo-bonito", "about 800 rooms", (26.0, 37.0)),
    ("casa-rinconada", "the great kiva", (28.0, 36.0)),
    ("pueblo-alto", "where the North Road begins", (31.0, 40.0)),
    ("pueblo-del-arroyo", None, (35.0, 42.0)),
    ("kin-kletso", None, (37.0, 45.0)),
    ("penasco-blanco", "near the supernova pictograph", (43.0, 55.0)),
]


def flyover(sc: Scene, out: Path, chunk: int, chunks: int, size=(1920, 1080), fov=48.0):
    path = camera_path(sc)
    n = len(path)
    lo, hi = chunk * n // chunks, (chunk + 1) * n // chunks
    when0 = sunrise_plus(FLY_DAY, FLY_MINUTES, sc)
    out.mkdir(parents=True, exist_ok=True)
    todo = [f for f in range(lo, hi) if not (out / f"frame_{f:05d}.jpg").exists()]
    if not todo:
        print(f"chunk {chunk}: all {hi - lo} frames already rendered", flush=True)
        return
    t0 = time.time()
    # One sun for the whole flight: moving it re-runs the shadow and
    # occlusion passes every frame, and over a minute it barely moves.
    sun = sun_at(when0)
    # One layer stack per chunk, sized to what this stretch of the flight sees.
    v = stack_for(sc, path[lo:hi:4] + [path[hi - 1]], fov, size, sun, tag=f"-c{chunk:02d}")
    print(f"layers ready {time.time() - t0:.0f}s", flush=True)
    v.sun(*sun)
    try:
        for f in todo:
            t = f / FPS
            eye, aim = path[f]
            raw = out / f"raw_{f:05d}.png"
            v.shot(eye, aim, fov, raw)
            im = finish(raw, DAWN)
            items = []
            for sid, sub, (a, b) in LABELS:
                fade = min(1.0, (t - a) / 0.8, (b - t) / 0.8)
                if fade > 0:
                    p = project(sc.site_world(sid), eye, aim, fov, size)
                    if p and p[2] < 9000:
                        items.append((p[0], p[1], sc.sites[sid]["name"], sub, fade))
            for x, y, text, sub, a, stem in place_labels(items, size):
                label(im, (x, y), text, sub, a, stem_px=stem)
            ta = min(1.0, max(0.0, min((t - 0.3) / 1.0, (6.5 - t) / 1.0)))
            caption(im, "Chaco Canyon · summer solstice sunrise",
                    f"June 21 · USGS 3DEP lidar · NAIP aerial photography · forge3d", alpha=ta)
            im.save(out / f"frame_{f:05d}.jpg", quality=93)
            raw.unlink()
            if (f - lo) % 10 == 0:
                print(f"frame {f} ({f - lo + 1}/{hi - lo}) {time.time() - t0:.0f}s", flush=True)
    finally:
        v.close()


def encode(frames: Path, out: Path):
    ff = shutil.which("ffmpeg")
    out.parent.mkdir(parents=True, exist_ok=True)
    common = ["-framerate", str(FPS), "-i", str(frames / "frame_%05d.jpg"), "-pix_fmt", "yuv420p",
              "-movflags", "+faststart", "-c:v", "libx264", "-preset", "slow"]
    subprocess.run([ff, "-y", "-loglevel", "error", *common, "-crf", "19", str(out.with_name(out.stem + "-1080.mp4"))], check=True)
    subprocess.run([ff, "-y", "-loglevel", "error", *common, "-crf", "26", "-vf", "scale=1280:-2",
                    str(out)], check=True)
    poster = sorted(frames.glob("frame_*.jpg"))[int(31 * FPS)]
    Image.open(poster).resize((1280, 720), Image.LANCZOS).save(out.with_name(out.stem + "-poster.jpg"), quality=86)
    print(f"wrote {out} and {out.stem}-1080.mp4")


def today(sc: Scene, out: Path, work: Path, size=(1600, 900)):
    """The canyon twenty minutes after today's sunrise, from the stills camera."""
    day = datetime.now(TZ).date()
    when = sunrise_plus(day, 20, sc)
    az, el = sun_at(when)
    fx, fy = sc.utm(sc.sites["fajada-butte"]["lon"], sc.sites["fajada-butte"]["lat"])
    bx, by = sc.utm(sc.sites["pueblo-bonito"]["lon"], sc.sites["pueblo-bonito"]["lat"])
    eye, aim = sc.world(fx + 2600, fy - 2300, 2330), sc.world(bx + 1200, by - 600, 1900)
    v = stack_for(sc, [(eye, aim)], 46, size, (az, el), tag="-today")
    try:
        v.sun(az, el)
        raw = work / "today.png"
        v.shot(eye, aim, 46, raw)
    finally:
        v.close()
    im = finish(raw, DAWN)
    im.save(out / "today.jpg", quality=86)
    (out / "today.json").write_text(json.dumps({"date": day.isoformat(), "local_time": when.isoformat(timespec="minutes"),
                                                "sun_azimuth": round(az, 1), "sun_elevation": round(el, 1)}))
    print(f"today {day} {when:%H:%M} sun {az:.1f}/{el:.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["stills", "flyover", "encode", "today", "path"])
    ap.add_argument("--prep", default="prep")
    ap.add_argument("--out", default="renders")
    ap.add_argument("--work", default="work")
    ap.add_argument("--frames", default="frames")
    ap.add_argument("--chunk", type=int, default=0)
    ap.add_argument("--chunks", type=int, default=1)
    ap.add_argument("--all", action="store_true",
                    help="flyover: render every chunk in turn (on one machine); finished frames are skipped")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--source", choices=["usgs", "prep"], default="usgs",
                    help="where fine terrain windows come from: 1 m 3DEP + NAIP (usgs) or the 5 m prep data")
    ap.add_argument("--only", nargs="*", help="stills: render just these shots")
    a = ap.parse_args()
    out, work = Path(a.out), Path(a.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    size = (a.width, a.width * 9 // 16)
    if a.mode == "encode":
        return encode(Path(a.frames), out / "chaco-flyover.mp4")
    sc = Scene(Path(a.prep), work, a.source)
    if a.mode == "stills":
        stills(sc, out, work, size, a.only)
    elif a.mode == "flyover":
        for c in (range(a.chunks) if a.all else [a.chunk]):
            flyover(sc, Path(a.frames), c, a.chunks, size)
    elif a.mode == "today":
        today(sc, out, work)
    elif a.mode == "path":
        print(len(camera_path(sc)), "frames")


if __name__ == "__main__":
    main()
