# aoi_to_slc

From an area of interest to a list of Sentinel-1 products to download: a webmap
to draw or paste the AOI, list the SLC / GRD scenes covering it, tick some and
write their S3 paths to a text file, one per line. A stripped-down Copernicus
Browser that stops where the download starts: the path file is what a
downloader takes, with an output folder, to fetch the products. Nothing is
downloaded by this module and no credentials are needed; the webmap chains it
with `download_products` (*Browse & download* tab) and with the pre/post
pipelines (*Pre / post* tab), whose search goes through `search_products` too.

- `aoi_to_slc.py` — the logic: the search (`search_products`), the path file
  (`write_path_file`, `s3_paths`, `format_s3_path`), the AOI helpers
  (`parse_aoi`, `save_aoi`), plus a notebook interface (`build_ui`, ipyleaflet +
  ipywidgets). Usable as a library without the widget packages.
- `aoi_to_slc.ipynb` — the interface inside a notebook, plus the calls from
  plain Python. A thin driver of the module, *not* a mirror of it.
- **The webmap in the browser** lives in `frontend/` at the repository root:
  `python rosarium.py webmap --open`. The server answers JSON routes
  (`frontend/api/aoi_to_slc.py`: parse the AOI, search, write, download) with
  this module's functions; the page does the map, the drawing and the list
  (`frontend/static/aoi_to_slc.js`, the *Browse & download* tab).

Path files land in `data/utils/` by default (`list.txt`, plus the AOI as
`list_aoi.geojson`); the folder's content is not versioned. `list.txt` is
also what `python rosarium.py download` (`features/download_products/`) reads
by default, so the two steps chain without an argument.

## Backend: the CDSE STAC catalogue, hit directly

Search hits `https://stac.dataspace.copernicus.eu/v1/search` with `requests`
(no `pystac-client`): a POST per page with the AOI as `intersects`, the date
range, a CQL2 filter on `sar:instrument_mode`, `sat:orbit_state` and
`platform`, sorted newest first, following the `next` links up to `max_items`.
Every STAC item exposes each file of the product as an asset with an
`s3://eodata/...` href; the `.SAFE` key is read from the manifest asset and is
what goes in the path file, in one of three styles:

| `style` | line written |
| --- | --- |
| `"mount"` (default) | `/eodata/Sentinel-1/SAR/IW_SLC__1S/2026/09/11/<product>.SAFE` |
| `"s3"` | `s3://eodata/Sentinel-1/SAR/IW_SLC__1S/2026/09/11/<product>.SAFE` |
| `"key"` | `eodata/Sentinel-1/SAR/IW_SLC__1S/2026/09/11/<product>.SAFE` |

**GRD means the COG variant.** CDSE distributes every GRD scene twice: the
original SAFE (`IW_GRDH_1S/.../..._614A.SAFE`) and a Cloud-Optimised one
(`IW_GRDH_1S-COG/.../..._DA9F_COG.SAFE`) — same values and same annotation
XML, measurements as Zstandard-compressed COG (~30 % lighter), always online
where an original older than a year may sit in cold storage. The two are
distinct products with different checksum suffixes, so one cannot be derived
from the other. The STAC catalogue lists the COG only, and this tool goes with
it: SNAP reads it from version 10 with the preprocessing graph unchanged. The
name difference matters only when cross-referencing by name with catalogues
that ignore the COG (ASF, HyP3). SLC products have no such variant.

Should the original GRD ever be needed, the CDSE OData catalogue lists both
variants (`productType` `IW_GRDH_1S` / `IW_GRDH_1S-COG`) with their `S3Path`:
a rewrite of `search_products` behind the same interface, nothing else.

**Why not `asf_search`.** Same Sentinel-1 scenes, but ASF only hands out its
own HTTPS zip URLs — no key in the CDSE `eodata` bucket, which is what the
downloader wants. `asf_search` stays the right tool for bursts as products and
InSAR baselines, which is outside this tool's scope.

## Setup

All dependencies come from conda-forge, in the `rosarium` env:

```
conda install -c conda-forge requests geopandas shapely ipyleaflet ipywidgets
```

No account, no token: the catalogue search is anonymous.

## Usage

**Browser** — the main way. From the repository root, in the `rosarium` env
(or double-click `launch/rosarium.bat`):

```
python rosarium.py webmap --open
python rosarium.py webmap --port 9000 --path-file C:/data/list.txt --style mount --days 60
```

The page opens on <http://localhost:8050>, on the *Pre / post* tab; this
feature is the *Browse & download* tab. Draw a polygon or a rectangle with
the toolbar on the map, or paste WKT / GeoJSON in the sidebar and *Use this
AOI* (the AOI is shared by the two tabs). Set the dates and criteria,
*Search*: the footprints appear on the map and in the list. Click a product in
the list or on the map to tick it (green); *All* / *None* for the whole list.
*Download* fetches the ticked products into `data/raw/<folder>/` (`vrac` when
the field is empty), writing `data/utils/<folder>.txt` and
`<folder>_aoi.geojson` on the way; a panel on the map follows the download.
*S3 paths only* previews the lines; *Write S3 paths* writes them to the path
file — overwriting it: one file is one selection — with the AOI next to it as
`<name>_aoi.geojson` unless *AOI alongside* is unticked. The header line
reports every step, errors in red. Closing the page stops the server, and a
running download with it (Ctrl+C in the terminal too; `--stay` to keep it
running).

`--path-file`, `--style`, `--center LAT LON`, `--zoom`, `--days` and
`--max-items` set the page's defaults; the path file stays editable in the page,
a relative one resolving against the repository root.

**Notebook.** Same interface inside `aoi_to_slc.ipynb`: fill the parameters
cell (path file, path style, map view), run the interface cell, draw or paste
the AOI, search, tick, *Write S3 paths*. See the notebook's opening cell.

**Python.**

```python
from aoi_to_slc import search_products, write_path_file

products = search_products(
    "POLYGON ((2.2 48.8, 2.5 48.8, 2.5 49.0, 2.2 49.0, 2.2 48.8))",
    "2026-08-01", "2026-09-12",
    product_type="SLC",          # or "GRD" (COG)
    mode="IW",                   # "IW", "EW", "SM", None
    orbit_direction="descending",
    platforms=["S1C", "S1D"],
)
# GeoDataFrame, newest first: name, datetime, platform, product_type, mode,
# orbit, relative_orbit, absolute_orbit, polarisations, size_gb, s3_key,
# geometry. products.attrs["truncated"] tells if max_items was hit.

chosen = products[products.relative_orbit == 110]
write_path_file(chosen, "data/utils/paris_orbit110.txt", style="mount", aoi=aoi)
```

From the repository root the import is
`from features.aoi_to_slc.aoi_to_slc import ...`; from this folder (the
notebook's case), `from aoi_to_slc import ...`.

`parse_aoi` accepts a shapely geometry, a GeoJSON dict, inline WKT or GeoJSON,
or the path of a WKT / GeoJSON file. Coordinates are lon/lat (EPSG:4326).

## Limits and things to know

- The CDSE front-end answers HTTP 429 to bursts of requests; the search retries
  with an exponential back-off. Listing a few hundred products takes a little
  longer, nothing more.
- Sentinel-1 SLC bursts as products are not covered: whole products only here.
  Which bursts of a product cover the AOI is `polygon_to_swaths_bursts`'s job.
- The browser page loads Leaflet, Leaflet.draw and the basemap tiles from the
  web — like the catalogue search, it needs an internet connection.
- The notebook interface needs the Jupyter widgets front-end (ipyleaflet,
  ipywidgets). It works in VS Code; if the map stays blank, use the browser page.
