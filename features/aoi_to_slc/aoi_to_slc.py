"""Sentinel-1 products from an area of interest: search CDSE, pick, list S3 paths.

Search goes through the CDSE STAC API, anonymously. Every STAC item carries the
S3 key of its product in the ``eodata`` bucket; the tool writes the paths of
the chosen products to a text file, one per line, for a downloader that takes
such a list and an output folder. Nothing is downloaded here.

Usable as a library — `search_products` returns a GeoDataFrame, `write_path_file`
writes the list — or through the ipyleaflet + ipywidgets interface returned by
`build_ui`, which `aoi_to_slc.ipynb` drives. The browser front-end
(`frontend/`, ``python rosarium.py webmap``) calls the same library functions.
"""

import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely import wkt as shapely_wkt
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

STAC_SEARCH_URL = "https://stac.dataspace.copernicus.eu/v1/search"

# STAC collection of each product type. The GRD collection holds the COG
# variant of the products only (``..._COG.SAFE``, Zstandard-compressed
# Cloud-Optimised GeoTIFF measurements, same values and same annotation XML as
# the original GRD, which CDSE also distributes under another name and folder).
# SNAP reads the COG variant from version 10; SLC products have no such variant.
COLLECTIONS = {"SLC": "sentinel-1-slc", "GRD": "sentinel-1-grd"}

# Values of the CDSE STAC properties the filters are built on
MODES = ("IW", "EW", "SM")
ORBIT_DIRECTIONS = ("ascending", "descending")
PLATFORMS = {"S1A": "sentinel-1a", "S1B": "sentinel-1b", "S1C": "sentinel-1c", "S1D": "sentinel-1d"}

# How the ``eodata/Sentinel-1/SAR/.../<product>.SAFE`` key of a product is
# written in the path file: what goes in front of it.
S3_PATH_STYLES = {
    "mount": "/",      # /eodata/...    the bucket mounted on a CDSE machine
    "s3": "s3://",     # s3://eodata/...
    "key": "",         # eodata/...     the bare key
}
DEFAULT_STYLE = "mount"

# Default path file: also what `rosarium.py download` reads
DEFAULT_PATH_FILE = Path(__file__).resolve().parents[2] / "data" / "utils" / "list.txt"

PAGE_SIZE = 100          # items per STAC page; the server accepts up to 200
DEFAULT_MAX_ITEMS = 500

RESULT_COLUMNS = [
    "name", "datetime", "platform", "product_type", "mode", "orbit",
    "relative_orbit", "absolute_orbit", "polarisations", "size_gb",
    "s3_key", "geometry",
]


# --- Area of interest ---------------------------------------------------------

def _geometry_from_geojson(obj):
    """Build a single shapely geometry from a parsed GeoJSON object.

    Accepts a bare geometry, a Feature, a FeatureCollection or a
    GeometryCollection; several features are merged into one geometry.
    """
    kind = obj.get("type")
    if kind == "FeatureCollection":
        geoms = [
            shape(f["geometry"]) for f in obj.get("features", []) if f.get("geometry")
        ]
    elif kind == "Feature":
        geoms = [shape(obj["geometry"])]
    elif kind == "GeometryCollection":
        geoms = [shape(g) for g in obj.get("geometries", [])]
    elif kind:
        geoms = [shape(obj)]
    else:
        raise ValueError("Unrecognised GeoJSON: no 'type' member")

    if not geoms:
        raise ValueError("GeoJSON holds no geometry")
    return geoms[0] if len(geoms) == 1 else unary_union(geoms)


def parse_aoi(aoi):
    """Parse an area of interest into a shapely geometry (lon/lat, EPSG:4326).

    Parameters
    ----------
    aoi : shapely geometry | dict | str | Path
        A shapely geometry is returned as is; a dict is read as GeoJSON; a
        string is the path of a WKT or GeoJSON file if such a file exists,
        otherwise inline WKT or inline GeoJSON. Inline GeoJSON is accepted here,
        unlike in polygon_to_swaths_bursts: the text comes from a widget, not
        from a command line.
    """
    if isinstance(aoi, BaseGeometry):
        return aoi
    if isinstance(aoi, dict):
        return _geometry_from_geojson(aoi)

    text = str(aoi).strip()
    if not text:
        raise ValueError("empty area of interest")
    # A path has no newline and stays short: cheap enough to probe
    if "\n" not in text and len(text) < 4096:
        try:
            if Path(text).is_file():
                text = Path(text).read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            pass  # not a usable path, treat the string as a geometry

    if text.startswith("{"):
        return _geometry_from_geojson(json.loads(text))
    try:
        return shapely_wkt.loads(text)
    except Exception as exc:  # shapely raises several unrelated types here
        raise ValueError(f"not a WKT geometry, not GeoJSON: {text[:60]!r}") from exc


def save_aoi(aoi, path):
    """Write the AOI to a file: GeoJSON for .geojson / .json, WKT otherwise."""
    geom = parse_aoi(aoi)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in (".geojson", ".json"):
        feature = {"type": "Feature", "properties": {}, "geometry": mapping(geom)}
        path.write_text(json.dumps(feature), encoding="utf-8")
    else:
        path.write_text(geom.wkt, encoding="utf-8")
    return path


# --- STAC search --------------------------------------------------------------

def _iso_bound(value, end=False):
    """Format one bound of the datetime range for the STAC query.

    A date covers its whole day: 00:00:00 as start bound, 23:59:59 as end
    bound. A datetime is taken as is (UTC when naive). A string is passed
    through when it holds a time, otherwise treated as a date.
    """
    if isinstance(value, str):
        value = value.strip()
        if "T" in value:
            # RFC 3339 wants a timezone: assume UTC when none is given
            return value if re.search(r"(Z|[+-]\d{2}:?\d{2})$", value) else value + "Z"
        value = date.fromisoformat(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    clock = "T23:59:59Z" if end else "T00:00:00Z"
    return value.strftime("%Y-%m-%d") + clock


def _cql2_filter(mode, orbit_direction, platforms):
    """CQL2-JSON filter from the optional criteria; None when there is none."""
    args = []
    if mode:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        args.append({"op": "=", "args": [{"property": "sar:instrument_mode"}, mode]})
    if orbit_direction:
        orbit_direction = orbit_direction.lower()
        if orbit_direction not in ORBIT_DIRECTIONS:
            raise ValueError(
                f"orbit_direction must be one of {ORBIT_DIRECTIONS}, got {orbit_direction!r}"
            )
        args.append({"op": "=", "args": [{"property": "sat:orbit_state"}, orbit_direction]})
    if platforms:
        if isinstance(platforms, str):
            platforms = [platforms]
        names = [PLATFORMS.get(p.upper(), p.lower()) for p in platforms]
        if len(names) == 1:
            args.append({"op": "=", "args": [{"property": "platform"}, names[0]]})
        else:
            args.append({"op": "in", "args": [{"property": "platform"}, names]})

    if not args:
        return None
    return args[0] if len(args) == 1 else {"op": "and", "args": args}


def _post_with_retry(url, body, attempts=5, timeout=60):
    """POST a STAC search, retrying on rate limiting and server errors.

    The CDSE front-end answers 429 as soon as requests come in quick
    succession — which paging through results does. A short exponential
    back-off is enough to get through.
    """
    for attempt in range(1, attempts + 1):
        response = requests.post(url, json=body, timeout=timeout)
        if response.status_code == 200:
            return response.json()
        if response.status_code in (429, 502, 503, 504) and attempt < attempts:
            time.sleep(2 ** attempt)
            continue
        raise RuntimeError(
            f"STAC search failed with HTTP {response.status_code}: {response.text[:200]}"
        )


def _item_record(item):
    """Flatten one STAC item into the row of the results table.

    The S3 key of the product — ``eodata/Sentinel-1/SAR/<type>/<date>/<name>.SAFE``
    — is read from the manifest asset. The size comes from the zip asset, found
    by its media type: the SLC collection names it ``product``, the GRD one
    ``Product``.
    """
    props = item["properties"]
    assets = item.get("assets", {})

    manifest = assets.get("safe_manifest", {}).get("href", "")
    if not manifest.startswith("s3://"):
        manifest = next(
            (a["href"] for a in assets.values() if a.get("href", "").startswith("s3://")),
            "",
        )
    match = re.match(r"s3://(.+?\.SAFE)/", manifest)
    s3_key = match.group(1) if match else None

    zip_asset = next(
        (a for a in assets.values() if a.get("type") == "application/zip"), {}
    )
    size = zip_asset.get("file:size")

    return {
        "name": item["id"],
        "datetime": pd.Timestamp(props.get("start_datetime") or props["datetime"]),
        "platform": item["id"][:3],
        "product_type": props.get("product:type"),
        "mode": props.get("sar:instrument_mode"),
        "orbit": props.get("sat:orbit_state"),
        "relative_orbit": props.get("sat:relative_orbit"),
        "absolute_orbit": props.get("sat:absolute_orbit"),
        "polarisations": "+".join(props.get("sar:polarizations", [])),
        "size_gb": round(size / 1e9, 2) if size else None,
        "s3_key": s3_key,
        "geometry": shape(item["geometry"]),
    }


def search_products(
    aoi,
    start,
    end,
    product_type="SLC",
    mode="IW",
    orbit_direction=None,
    platforms=None,
    max_items=DEFAULT_MAX_ITEMS,
):
    """Sentinel-1 products intersecting the AOI in the CDSE STAC catalogue.

    Parameters
    ----------
    aoi : shapely geometry | dict | str | Path
        Area of interest, lon/lat (EPSG:4326) — see `parse_aoi`.
    start, end : date | datetime | str
        Acquisition window, both bounds inclusive. Dates cover their whole day.
    product_type : {"SLC", "GRD"}
        Which collection to search. GRD products are the COG variant — see
        `COLLECTIONS`.
    mode : {"IW", "EW", "SM"} | None
        Acquisition mode; None keeps them all.
    orbit_direction : {"ascending", "descending"} | None
    platforms : str | list of str | None
        "S1A", "S1B", "S1C", "S1D" (or the STAC names "sentinel-1a"...).
    max_items : int
        Stop paging after that many products; `result.attrs["truncated"]`
        says whether the catalogue held more.

    Returns
    -------
    GeoDataFrame
        One row per product, newest first, columns `RESULT_COLUMNS`.
        `s3_key` is the key of the product in the ``eodata`` bucket, what
        `write_path_file` writes out.
    """
    product_type = product_type.upper()
    if product_type not in COLLECTIONS:
        raise ValueError(f"product_type must be one of {tuple(COLLECTIONS)}, got {product_type!r}")

    body = {
        "collections": [COLLECTIONS[product_type]],
        "intersects": mapping(parse_aoi(aoi)),
        "datetime": f"{_iso_bound(start)}/{_iso_bound(end, end=True)}",
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
        "limit": min(PAGE_SIZE, max_items),
    }
    cql2 = _cql2_filter(mode, orbit_direction, platforms)
    if cql2:
        body["filter-lang"] = "cql2-json"
        body["filter"] = cql2

    records = []
    truncated = False
    url = STAC_SEARCH_URL
    while True:
        page = _post_with_retry(url, body)
        records.extend(_item_record(item) for item in page.get("features", []))
        # The next link carries the full body of the following request,
        # continuation token included
        nxt = next((l for l in page.get("links", []) if l.get("rel") == "next"), None)
        if nxt is None:
            break
        if len(records) >= max_items:
            truncated = True
            break
        url, body = nxt["href"], nxt["body"]

    result = gpd.GeoDataFrame(records[:max_items], columns=RESULT_COLUMNS, crs="EPSG:4326")
    result.attrs["truncated"] = truncated
    return result


# --- Path file ----------------------------------------------------------------

def format_s3_path(s3_key, style=DEFAULT_STYLE):
    """One product key written in the given style — see `S3_PATH_STYLES`."""
    if style not in S3_PATH_STYLES:
        raise ValueError(f"style must be one of {tuple(S3_PATH_STYLES)}, got {style!r}")
    return S3_PATH_STYLES[style] + s3_key


def s3_paths(products, style=DEFAULT_STYLE):
    """The S3 path of each product, in the order given."""
    rows = products.to_dict("records") if hasattr(products, "to_dict") else list(products)
    missing = [r["name"] for r in rows if not r.get("s3_key")]
    if missing:
        raise ValueError(f"no S3 key in the catalogue entry of: {', '.join(missing)}")
    return [format_s3_path(r["s3_key"], style) for r in rows]


def write_path_file(products, path, style=DEFAULT_STYLE, aoi=None):
    """Write the S3 path of each product to a text file, one per line.

    The file is overwritten: one file is one selection. Meant for a downloader
    that takes such a list and an output folder.

    Parameters
    ----------
    products : GeoDataFrame | iterable of mappings
        Rows of `search_products`; each needs `name` and `s3_key`.
    path : str | Path
        The text file; missing parent folders are created.
    style : {"mount", "s3", "key"}
        Form of each line — see `S3_PATH_STYLES`. Default "mount":
        ``/eodata/Sentinel-1/SAR/.../<product>.SAFE``.
    aoi : optional
        When given, also written next to the file as ``<stem>_aoi.geojson``,
        so the products and the area they were chosen for travel together.

    Returns
    -------
    Path
        The text file.
    """
    lines = s3_paths(products, style)
    if not lines:
        raise ValueError("no product to write")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if aoi is not None:
        save_aoi(aoi, path.with_name(f"{path.stem}_aoi.geojson"))
    return path


# --- Interface ----------------------------------------------------------------

def _feature_collection(gdf, columns=("name",)):
    """GeoJSON FeatureCollection of a GeoDataFrame, keeping only some columns."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {c: str(row[c]) for c in columns},
                "geometry": mapping(row.geometry),
            }
            for _, row in gdf.iterrows()
        ],
    }


def _result_label(row):
    """One line of the results list."""
    size = f"{row.size_gb:5.1f} GB" if pd.notna(row.size_gb) else "     ?"
    orbit = (row.orbit or "?")[:4]
    rel = f"{int(row.relative_orbit):3d}" if pd.notna(row.relative_orbit) else "  ?"
    return (
        f"{row.datetime:%Y-%m-%d %H:%M}  {row.platform}  {orbit}  "
        f"rel {rel}  {row.product_type}  {row.polarisations}  {size}"
    )


class AoiToSlcUI:
    """Webmap interface: draw or paste an AOI, search, tick products, write the list.

    Everything the user does is also reachable from Python: `aoi` (shapely
    geometry), `results` (the last search, a GeoDataFrame), `selected` (the
    ticked rows), `s3_paths` (their S3 paths) and `written` (the path files
    written so far).
    """

    def __init__(
        self,
        path_file=DEFAULT_PATH_FILE,
        style=DEFAULT_STYLE,
        center=(46.5, 2.5),
        zoom=6,
        start=None,
        end=None,
        max_items=DEFAULT_MAX_ITEMS,
    ):
        # Imported here so that the library functions above work without the
        # widget packages installed
        import ipywidgets as w
        from ipyleaflet import DrawControl, GeoJSON, LayersControl, Map, basemaps

        format_s3_path("", style)  # validate the style before anything is built
        self.style = style
        self.max_items = max_items
        self.aoi = None
        self.results = None
        self.written = []

        today = date.today()
        end = end or today
        start = start or end - timedelta(days=30)

        # --- map ---
        self.map = Map(
            center=center, zoom=zoom, basemap=basemaps.OpenStreetMap.Mapnik,
            scroll_wheel_zoom=True, layout=w.Layout(height="480px", width="100%"),
        )
        shape_style = {"color": "#d62728", "weight": 2, "fillOpacity": 0.05}
        self.draw = DrawControl(
            polygon={"shapeOptions": shape_style},
            rectangle={"shapeOptions": shape_style},
            circle={}, circlemarker={}, marker={}, polyline={},
            edit=False, remove=False,
        )
        self.draw.on_draw(self._on_draw)
        self.map.add(self.draw)

        self.aoi_layer = GeoJSON(
            data={"type": "FeatureCollection", "features": []}, name="AOI",
            style={"color": "#d62728", "weight": 2, "fillOpacity": 0.05},
        )
        self.results_layer = GeoJSON(
            data={"type": "FeatureCollection", "features": []}, name="Footprints",
            style={"color": "#555555", "weight": 1, "fillOpacity": 0.02},
            hover_style={"color": "#1f77b4", "weight": 2, "fillOpacity": 0.1},
        )
        self.selected_layer = GeoJSON(
            data={"type": "FeatureCollection", "features": []}, name="Selected",
            style={"color": "#1f77b4", "weight": 3, "fillOpacity": 0.15},
        )
        self.results_layer.on_hover(self._on_hover)
        for layer in (self.results_layer, self.selected_layer, self.aoi_layer):
            self.map.add(layer)
        self.map.add(LayersControl(position="topright"))

        # --- AOI text ---
        self.aoi_text = w.Textarea(
            placeholder="WKT or GeoJSON, lon/lat — or draw on the map",
            layout=w.Layout(width="100%", height="70px"),
        )
        self.use_text_btn = w.Button(description="Use this AOI", icon="check")
        self.clear_btn = w.Button(description="Clear", icon="trash")
        self.use_text_btn.on_click(lambda _: self._guard(self._use_text))
        self.clear_btn.on_click(lambda _: self._guard(self._clear))

        # --- criteria ---
        self.start = w.DatePicker(description="From", value=start)
        self.end = w.DatePicker(description="To", value=end)
        self.product = w.Dropdown(
            description="Product", options=[("SLC", "SLC"), ("GRD (COG)", "GRD")], value="SLC"
        )
        self.mode = w.Dropdown(description="Mode", options=["IW", "EW", "SM", "any"], value="IW")
        self.orbit = w.Dropdown(
            description="Orbit", options=["any", "ascending", "descending"], value="any"
        )
        self.platform = w.Dropdown(
            description="Platform", options=["any"] + list(PLATFORMS), value="any"
        )
        self.search_btn = w.Button(description="Search", icon="search", button_style="primary")
        self.search_btn.on_click(lambda _: self._guard(self._search))
        self.status = w.HTML("Draw an AOI on the map or paste one below.")

        # --- results ---
        self.list = w.SelectMultiple(options=[], rows=12, layout=w.Layout(width="100%"))
        self.list.observe(self._on_select, names="value")
        self.hover = w.HTML("&nbsp;")

        # --- path file ---
        self.path_file = w.Text(
            description="Path file", value=str(path_file), layout=w.Layout(width="60%")
        )
        # The downstream tools take "products + AOI": writing the AOI next to
        # the list keeps the two together
        self.with_aoi = w.Checkbox(
            description="AOI alongside", value=True, indent=False,
            tooltip="Also write the AOI as <path file>_aoi.geojson, next to the list",
        )
        self.write_btn = w.Button(
            description="Write S3 paths", icon="file-text", button_style="success"
        )
        self.write_btn.on_click(lambda _: self._guard(self._write))
        self.preview = w.Textarea(
            layout=w.Layout(width="100%", height="120px"), disabled=True,
            placeholder="the lines that will be written",
        )

        self.widget = w.VBox([
            self.map,
            w.HBox([self.aoi_text, w.VBox([self.use_text_btn, self.clear_btn])]),
            w.HBox([self.start, self.end, self.product, self.mode]),
            w.HBox([self.orbit, self.platform, self.search_btn]),
            self.status,
            self.list,
            self.hover,
            w.HBox([self.path_file, self.with_aoi, self.write_btn]),
            self.preview,
        ])

    def _ipython_display_(self):
        from IPython.display import display
        display(self.widget)

    # --- helpers ---

    def _guard(self, action):
        """Run a handler and show its error: widget callbacks swallow exceptions."""
        try:
            action()
        except Exception as exc:  # noqa: BLE001 — anything must reach the user
            self.status.value = f'<span style="color:#d62728">{type(exc).__name__}: {exc}</span>'

    def _set_aoi(self, geom, fit=True):
        self.aoi = geom
        self.aoi_layer.data = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {}, "geometry": mapping(geom)}],
        }
        if fit:
            minx, miny, maxx, maxy = geom.bounds
            self.map.fit_bounds([[miny, minx], [maxy, maxx]])

    @property
    def selected(self):
        """The ticked rows of the last search, as a GeoDataFrame."""
        if self.results is None:
            return None
        return self.results.iloc[list(self.list.value)]

    @property
    def s3_paths(self):
        """The S3 paths of the ticked rows, as they would be written."""
        rows = self.selected
        return [] if rows is None else s3_paths(rows, self.style)

    # --- handlers ---

    def _on_draw(self, control, action, geo_json):
        if action != "created":
            return
        geom = shape(geo_json["geometry"])
        # The DrawControl keeps its own copy of the shape: drop it and show the
        # AOI through aoi_layer instead, so only one AOI ever exists
        control.clear()
        self._set_aoi(geom, fit=False)
        self.aoi_text.value = geom.wkt
        self.status.value = f"AOI set from the map ({geom.geom_type}, {geom.area:.4f} deg²)."

    def _use_text(self):
        geom = parse_aoi(self.aoi_text.value)
        self._set_aoi(geom)
        self.status.value = f"AOI set from text ({geom.geom_type}, {geom.area:.4f} deg²)."

    def _clear(self):
        self.aoi = None
        self.results = None
        self.aoi_text.value = ""
        self.list.options = []
        self.preview.value = ""
        for layer in (self.aoi_layer, self.results_layer, self.selected_layer):
            layer.data = {"type": "FeatureCollection", "features": []}
        self.status.value = "Cleared."

    def _on_hover(self, event=None, feature=None, **kwargs):
        if feature:
            self.hover.value = feature["properties"].get("name", "")

    def _search(self):
        if self.aoi is None:
            raise ValueError("no AOI: draw one on the map or paste one and click 'Use this AOI'")
        if self.start.value is None or self.end.value is None:
            raise ValueError("both dates are needed")
        self.status.value = "Searching…"
        self.results = search_products(
            self.aoi, self.start.value, self.end.value,
            product_type=self.product.value,
            mode=None if self.mode.value == "any" else self.mode.value,
            orbit_direction=None if self.orbit.value == "any" else self.orbit.value,
            platforms=None if self.platform.value == "any" else self.platform.value,
            max_items=self.max_items,
        )
        self.list.options = [
            (_result_label(row), i) for i, row in enumerate(self.results.itertuples(index=False))
        ]
        self.results_layer.data = _feature_collection(self.results)
        self.selected_layer.data = {"type": "FeatureCollection", "features": []}
        self.preview.value = ""
        n = len(self.results)
        more = " — more in the catalogue, narrow the dates" if self.results.attrs.get("truncated") else ""
        total = self.results.size_gb.sum()
        self.status.value = f"{n} product(s), {total:.0f} GB in total{more}."

    def _on_select(self, change):
        if self.results is None:
            return
        rows = self.selected
        self.selected_layer.data = _feature_collection(rows)
        self.preview.value = "\n".join(self.s3_paths)
        if len(rows):
            self.status.value = f"{len(rows)} selected, {rows.size_gb.sum():.1f} GB."

    def _write(self):
        rows = self.selected
        if rows is None or rows.empty:
            raise ValueError("nothing selected")
        path = write_path_file(
            rows, self.path_file.value, style=self.style,
            aoi=self.aoi if self.with_aoi.value else None,
        )
        self.written.append(path)
        aoi_note = f" (+ {path.stem}_aoi.geojson)" if self.with_aoi.value else ""
        self.status.value = f"{len(rows)} S3 path(s) written to {path}{aoi_note}."


def build_ui(**kwargs):
    """Build the webmap interface; see `AoiToSlcUI` for the keyword arguments."""
    return AoiToSlcUI(**kwargs)
