"""JSON routes of the aoi_to_slc feature: parse the AOI, search, write the list, download.

Thin adapters between the page (`static/aoi_to_slc.js`, and the search of
`static/pre_post.js`) and the library functions of `features/aoi_to_slc` and
`features/download_products`. Each route takes the parsed JSON body and the
server config, and returns something `json.dumps` accepts. The download runs
as a background job (`frontend/jobs.py`).
"""

import math
from pathlib import Path

import pandas as pd
from shapely.affinity import translate
from shapely.geometry import mapping
from shapely.ops import unary_union

from features.aoi_to_slc.aoi_to_slc import (
    DEFAULT_PATH_FILE,
    format_s3_path,
    parse_aoi,
    search_products,
    split_antimeridian,
    write_path_file,
)
from features.download_products.download_products import DEFAULT_FOLDER, parallel_download
from frontend import jobs

# Where a download job writes its path file and AOI: <folder>.txt and
# <folder>_aoi.geojson, one pair per download folder
UTILS_DIR = DEFAULT_PATH_FILE.parent


def _clean(value):
    """A cell of the results table as something json.dumps accepts."""
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):  # numpy scalar
        return value.item()
    return value


def _feature(row, style, aoi):
    """One row of `search_products` as a GeoJSON feature, path included.

    Two more properties serve the pre/post selection: `covers_aoi` (the
    footprint holds the whole AOI, not just a part) and `centroid` (lon, lat
    of the footprint, to tell products framed alike on the same track).
    """
    props = {k: _clean(v) for k, v in row._asdict().items() if k != "geometry"}
    props["path"] = format_s3_path(props["s3_key"], style) if props["s3_key"] else None
    props["covers_aoi"] = bool(row.geometry.covers(aoi))
    centroid = row.geometry.centroid
    props["centroid"] = [centroid.x, centroid.y]
    return {"type": "Feature", "properties": props, "geometry": mapping(row.geometry)}


def api_parse_aoi(body, _config):
    """The AOI back in [-180, 180]; `parts` holds its two halves when it crosses ±180."""
    parts = split_antimeridian(body.get("text", ""))
    # A crossing AOI is shown whole, its eastern half past 180 so that the map
    # draws one shape rather than a band around the world
    geom = parts[0] if len(parts) == 1 else unary_union([parts[0], translate(parts[1], xoff=360)])
    return {
        "geometry": mapping(geom), "type": geom.geom_type,
        "parts": [{"geometry": mapping(p), "wkt": p.wkt} for p in parts] if len(parts) > 1 else None,
    }


def api_search(body, config):
    for key in ("aoi", "start", "end"):
        if not body.get(key):
            raise ValueError(f"{key} is missing")
    parts = split_antimeridian(body["aoi"])
    if len(parts) > 1:
        raise ValueError("the AOI crosses the antimeridian: use one half, then the other")
    aoi = parts[0]
    products = search_products(
        aoi, body["start"], body["end"],
        product_type=body.get("product_type") or "SLC",
        mode=body.get("mode") or None,
        orbit_direction=body.get("orbit_direction") or None,
        platforms=body.get("platforms") or None,
        max_items=config["max_items"],
    )
    print(f"  search: {len(products)} product(s)"
          + (" (truncated)" if products.attrs["truncated"] else ""), flush=True)
    return {
        "type": "FeatureCollection",
        "features": [_feature(row, config["style"], aoi) for row in products.itertuples(index=False)],
        "truncated": products.attrs["truncated"],
    }


def api_write(body, config):
    products = body.get("products") or []
    if not products:
        raise ValueError("nothing selected")
    path = Path(body.get("path_file") or config["path_file"])
    if not path.is_absolute():
        path = Path(config["root"]) / path
    aoi = body.get("aoi")
    written = write_path_file(products, path, style=config["style"], aoi=aoi)
    aoi_name = f"{written.stem}_aoi.geojson" if aoi else None
    print(f"  write: {len(products)} path(s) -> {written}" + (f" (+ {aoi_name})" if aoi_name else ""),
          flush=True)
    return {"path": str(written), "aoi_path": aoi_name, "count": len(products)}


def api_download(body, config):
    """Download the products into data/raw/<folder>/, as a background job."""
    products = body.get("products") or []
    if not products:
        raise ValueError("nothing selected")
    folder = jobs.folder_name(body.get("folder"), DEFAULT_FOLDER)
    list_path = UTILS_DIR / f"{folder}.txt"
    aoi = body.get("aoi")

    def run(reporter):
        reporter.plan(["download"])
        write_path_file(products, list_path, style=config["style"], aoi=aoi)
        reporter.log(f"{len(products)} path(s) written to {list_path}")
        dest = parallel_download(list_path, folder, reporter=reporter)
        return {"raw": str(dest)}

    return jobs.start(f"download -> data/raw/{folder}", run)


ROUTES = {
    "GET": {},
    "POST": {
        "/api/parse_aoi": api_parse_aoi, "/api/search": api_search,
        "/api/write": api_write, "/api/download": api_download,
    },
}
