"""JSON routes of the aoi_to_slc feature: parse the AOI, search, write the list.

Thin adapters between the page (`static/aoi_to_slc.js`) and the library
functions of `features/aoi_to_slc`. Each route takes the parsed JSON body and
the server config, and returns something `json.dumps` accepts.
"""

import math
from pathlib import Path

import pandas as pd
from shapely.geometry import mapping

from features.aoi_to_slc.aoi_to_slc import (
    format_s3_path,
    parse_aoi,
    search_products,
    write_path_file,
)


def _clean(value):
    """A cell of the results table as something json.dumps accepts."""
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):  # numpy scalar
        return value.item()
    return value


def _feature(row, style):
    """One row of `search_products` as a GeoJSON feature, path included."""
    props = {k: _clean(v) for k, v in row._asdict().items() if k != "geometry"}
    props["path"] = format_s3_path(props["s3_key"], style) if props["s3_key"] else None
    return {"type": "Feature", "properties": props, "geometry": mapping(row.geometry)}


def api_parse_aoi(body, _config):
    geom = parse_aoi(body.get("text", ""))
    return {"geometry": mapping(geom), "type": geom.geom_type}


def api_search(body, config):
    for key in ("aoi", "start", "end"):
        if not body.get(key):
            raise ValueError(f"{key} is missing")
    products = search_products(
        body["aoi"], body["start"], body["end"],
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
        "features": [_feature(row, config["style"]) for row in products.itertuples(index=False)],
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


ROUTES = {
    "GET": {},
    "POST": {"/api/parse_aoi": api_parse_aoi, "/api/search": api_search, "/api/write": api_write},
}
