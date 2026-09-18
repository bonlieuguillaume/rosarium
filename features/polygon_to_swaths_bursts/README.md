# polygon → swaths & bursts (Sentinel-1 SLC)

Given a Sentinel-1 SLC product and an area of interest — inline WKT, or a WKT or
GeoJSON file — return the sub-swaths and the bursts that the AOI intersects.

---

## 1. The problem

A Sentinel-1 IW SLC product covers ~250 × 170 km and weighs 4–8 GB; an area of
interest usually covers a few square kilometres of it. Processing the whole product
to study that patch wastes hours of computation and tens of gigabytes of disk.

Every SAR toolbox offers a burst selection step (`TOPSAR-Split` in SNAP, its
equivalents in ISCE or GAMMA), but they all expect the answer to be already known:
*which* sub-swath, and *which* burst numbers inside it. Finding them by hand means
eyeballing burst outlines in a GUI and writing down indices — slow, error-prone, and
impossible to script over a stack of a hundred dates. This tool answers it
programmatically:

> input: a path to an SLC product + a polygon (WKT or GeoJSON)
> output: `{"IW1": [1, 2], "IW2": [2, 3]}`

It needs only conda-forge packages (numpy, shapely, geopandas), and never opens the
multi-gigabyte image data.

---

## 2. How Sentinel-1 IW products are organised

**Sub-swaths.** In IW mode (Interferometric Wide swath), the radar cycles its
antenna across three adjacent sub-swaths — **IW1** (closest to nadir), **IW2**,
**IW3** — spanning the ~250 km swath; the EW mode works the same way with five.
Adjacent sub-swaths overlap by 1–2 km, so the coverage has no gap.

**Bursts.** The antenna is also steered *along* the track (the TOPS technique),
chopping each sub-swath into short takes called **bursts** of about 20 km, around
nine per sub-swath in a standard slice. Consecutive bursts overlap by roughly a
kilometre — the margin that lets the deburst step stitch them seamlessly, and that
interferometry exploits for azimuth calibration (ESD).

**On disk.** A `.SAFE` product is a directory (often distributed zipped):

```
S1B_IW_SLC__1SDV_20170804T215105_..._B333.SAFE/
├── measurement/            one GeoTIFF per sub-swath × polarisation (the pixels)
├── annotation/             one XML per sub-swath × polarisation (the metadata)
│   ├── calibration/
│   └── rfi/                radio-frequency-interference reports
└── manifest.safe
```

Within a measurement file, bursts are stacked as **tiles of identical size**,
`linesPerBurst` lines each. Valid data does not fill its tile: the edges, where the
synthetic aperture is incomplete, are zeroed (*black-fill*), the real extent being
recorded line by line in `firstValidSample` / `lastValidSample`.

---

## 3. The method

Everything needed is in the annotation XML; the image data is never read.

**Step 1 — Collect the annotation files.** `annotation/s1*.xml`, read straight from
inside the `.zip` when the product is still archived. `calibration/` and `rfi/` are
skipped: same header structure, no geometry.

**Step 2 — Deduplicate polarisations.** One annotation file per sub-swath *and* per
polarisation (6 for a dual-pol IW product); VV and VH image the same ground, so only
the first polarisation of each sub-swath is kept.

**Step 3 — Cut the image into bursts.** From `swathTiming/linesPerBurst` and the
burst list: burst *k* occupies azimuth lines `[k · linesPerBurst,
(k+1) · linesPerBurst]`.

**Step 4 — Turn line numbers into ground coordinates.** The `geolocationGrid` is a
sparse list of tie points mapping `(line, pixel) → (latitude, longitude)`. The
decisive property: **its rows sit on the burst boundaries**, so no interpolation is
needed.

```
line=0     •  •  •  •  •  …  •      ← top edge of burst 1
line=1500  •  •  •  •  •  …  •      ← burst 1 / burst 2 boundary
line=3000  •  •  •  •  •  …  •      ← burst 2 / burst 3 boundary
line=4499  •  •  •  •  •  …  •      ← last line of the image
```

**Step 5 — Rebuild each burst footprint.** Take the grid row at the burst's first
line left to right, then the row at its last line right to left, and close the ring:

```
first line, increasing pixel  →   A ─ B ─ C ─ D ─ E
                                  │               │
last line, decreasing pixel   ←   J ─ I ─ H ─ G ─ F
```

Reversing the second row keeps the ring from crossing itself. Keeping every tie
point rather than just four corners lets the polygon follow the burst's real
curvature. The row is picked as the one *closest* to the computed boundary rather
than strictly equal to it — the last grid row sits at `numberOfLines − 1`.

**Step 6 — Parse the area of interest.** WKT inline or from a file, GeoJSON from a
file only — quoting JSON on a command line is error-prone, so inline GeoJSON strings
are rejected with an explicit message. The format is detected from the content, not
from the file extension; a multi-feature `FeatureCollection` is merged into one
geometry. Coordinates in lon/lat (EPSG:4326), the convention of both Sentinel-1
annotations and GeoJSON (RFC 7946).

**Step 7 — Handle the antimeridian.** Longitude jumps from +180 to −180 in the
Pacific, which would turn a straddling footprint into a polygon wrapping the wrong
way around the globe. When a ring's longitudes span more than 180° — impossible for
a genuine burst — negative longitudes are shifted by +360. The AOI gets the same
treatment and is tested together with its ±360° copies, so the frames match either
way.

**Step 8 — Intersect and summarise.** Footprints go into a GeoDataFrame
(EPSG:4326), shapely tests them against the AOI, and the hits are grouped into
`{swath: [burst numbers]}` — 1-based within each sub-swath, as in SNAP's
`TOPSAR-Split`.

### Strict mode vs. coarse mode

Footprints are **edge-matched**: consecutive bursts share their boundary row and
touch without overlapping, whereas the real valid data overlaps by about a
kilometre — asymmetrically, since the upstream burst extends past the seam while the
downstream one starts with a few hundred metres of black-fill *after* it.

So an AOI whose edge falls near a seam may need a burst the strict test misses.
`coarse=True` (`--coarse`) dilates the footprints by `coarse_margin` (0.02° ≈ 2 km)
**for the test only**, returning those neighbours; the geometries in the result stay
undilated. Use it whenever the AOI is not comfortably inside a single burst — one
extra burst is cheap, and the deburst step handles the duplicated strip cleanly.

### Known limits

- Footprints derive from the geolocation grid, not from `firstValidSample` /
  `lastValidSample`; they are accurate to roughly a kilometre near burst edges.
- Burst numbers are local to the product — they are not the ESA global burst IDs.
- The AOI must be in lon/lat WGS84; a polygon in a projected CRS (Lambert-93, UTM)
  must be reprojected first, and `POLYGON((lon lat, …))` is longitude-first.
- An AOI legitimately wider than 180° of longitude would be mistaken for an
  antimeridian crossing.

---

## 4. Usage

### Install

All dependencies come from conda-forge, in the `rosarium` env:

```
conda install -c conda-forge numpy shapely geopandas folium
```

### Command line

From the repository root, in the `rosarium` env:

```bash
# inline WKT, strict test
python rosarium.py bursts --slc-path product.SAFE --polygon "POLYGON ((2.2 48.8, 2.5 48.8, 2.5 49.0, 2.2 49.0, 2.2 48.8))"

# AOI read from a GeoJSON file, recall-oriented, JSON output
python rosarium.py bursts --slc-path product.zip --polygon aoi.geojson --coarse --json

# also export the footprints of the selected bursts
python rosarium.py bursts --slc-path product.zip --polygon aoi.wkt --geojson hits.geojson
```

```
Intersecting swaths: IW1, IW2
  IW1: bursts 1, 2
  IW2: bursts 2, 3
```

| Option | Effect |
| --- | --- |
| `--slc-path PATH` | **required** — the `.SAFE` directory or `.zip` archive |
| `--polygon AOI` | **required** — inline WKT, or a path to a WKT / GeoJSON file |
| `--coarse` | dilate footprints before the test (favours recall) |
| `--coarse-margin DEG` | dilation margin in degrees, default `0.02` (~2 km) |
| `--json` | print `{swath: [bursts]}` as JSON instead of text |
| `--geojson PATH` | write the footprints of the selected bursts to a file |

`python rosarium.py bursts --help` prints the same description as this
section. The module also runs on its own with the same options:
`python features/polygon_to_swaths_bursts/polygon_to_swaths_bursts.py ...`.

### Python

```python
from polygon_to_swaths_bursts import get_intersecting_bursts, load_burst_footprints

hits, summary = get_intersecting_bursts(
    "product.SAFE",
    "aoi.geojson",        # or a .wkt file, an inline WKT string, or a dict
    coarse=True,          # optional, default False
    coarse_margin=0.02,   # optional
)
# summary -> {"IW1": [1, 2], "IW2": [2, 3]}
# hits    -> GeoDataFrame (swath, polarisation, burst, geometry)

footprints = load_burst_footprints("product.SAFE")   # every burst, for inspection
```

From the repository root the import is
`from features.polygon_to_swaths_bursts.polygon_to_swaths_bursts import ...`;
from this folder (the notebook's case), `from polygon_to_swaths_bursts import ...`.

### Notebook

`polygon_to_swaths_bursts.ipynb` holds the same functions cell by cell, plus a
folium map that draws every burst of the product — the selected ones in red, the
others in grey, the AOI in blue. It is the quickest way to check a result visually.
If the map does not render in VS Code, save it and open it in a browser:
`m.save("map.html")`.

### Files

| File | Role |
| --- | --- |
| `polygon_to_swaths_bursts.py` | library + command-line interface |
| `polygon_to_swaths_bursts.ipynb` | same functions, interactive, with a map |
