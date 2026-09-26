"""JSON routes of the pre/post pipelines: check the output, run the whole chain.

A run is one background job (`frontend/jobs.py`): write the path file and the
AOI (`data/utils/<raw folder>.txt`, `<raw folder>_aoi.geojson`), download the
products into `data/raw/<raw folder>/`, then `main_preprocess` (4 SLC) or
`main_preprocess_grd` (2 GRD) into `data/preprocessed/pre_post/<name>/`, with
the job's log there as `<name>.log`. The search and the S3-path file go
through the routes of `aoi_to_slc`; the page is `static/pre_post.js`.
"""

from pathlib import PurePosixPath

from features.aoi_to_slc.aoi_to_slc import DEFAULT_PATH_FILE, write_path_file
from features.download_products.download_products import (
    DEFAULT_FOLDER,
    RAW_DIR,
    parallel_download,
)
from features.pre_post.pre_post_backscatter.pre_post_backscatter import main_preprocess_grd
from features.pre_post.pre_post_backscatter_coherence.pre_post_backscatter_coherence import (
    main_preprocess,
)
from features.snap_gpt.snap_gpt import DEFAULT_GPT, PRE_POST_DIR
from frontend import jobs

UTILS_DIR = DEFAULT_PATH_FILE.parent

# How many products each mode takes, per side of the event
PER_SIDE = {"GRD": 1, "SLC": 2}


def _names(body):
    """(raw folder, run name): the run name defaults to the raw folder."""
    folder = jobs.folder_name(body.get("raw_folder"), DEFAULT_FOLDER)
    return folder, jobs.folder_name(body.get("name"), folder)


def _outputs(name):
    folder = PRE_POST_DIR / name
    return [folder / f"{name}_pre.tif", folder / f"{name}_post.tif"]


def _side(body, key, count):
    """The products of one side, oldest first: pre1 before pre2."""
    products = body.get(key) or []
    if len(products) != count:
        raise ValueError(f"{count} {key} product(s) expected, got {len(products)}")
    for p in products:
        if not p.get("s3_key"):
            raise ValueError(f"no S3 key for {p.get('name')}")
    return sorted(products, key=lambda p: p.get("datetime") or "")


def _local_path(folder, product):
    """Where a product lands once downloaded: data/raw/<folder>/<product>.SAFE."""
    return RAW_DIR / folder / PurePosixPath(product["s3_key"]).name


def api_check(body, _config):
    """The GeoTIFFs a run would overwrite, so the page can ask first."""
    folder, name = _names(body)
    return {
        "raw_folder": folder,
        "name": name,
        "existing": [str(p) for p in _outputs(name) if p.exists()],
    }


def api_run(body, config):
    mode = body.get("mode")
    if mode not in PER_SIDE:
        raise ValueError(f"mode must be one of {tuple(PER_SIDE)}, got {mode!r}")
    aoi = body.get("aoi")
    if not aoi:
        raise ValueError("no AOI")
    pre = _side(body, "pre", PER_SIDE[mode])
    post = _side(body, "post", PER_SIDE[mode])
    if max(p["datetime"] for p in pre) >= min(p["datetime"] for p in post):
        raise ValueError("every pre product must be older than every post product")
    folder, name = _names(body)
    if not DEFAULT_GPT:
        raise FileNotFoundError(
            "SNAP's gpt not found: install SNAP, or set SNAP_GPT, then restart the webmap"
        )
    existing = [p for p in _outputs(name) if p.exists()]
    if existing and not body.get("overwrite"):
        raise FileExistsError(f"{existing[0]} already exists")

    list_path = UTILS_DIR / f"{folder}.txt"
    aoi_path = list_path.with_name(f"{list_path.stem}_aoi.geojson")

    def run(reporter):
        reporter.plan(["download"])
        write_path_file(pre + post, list_path, style=config["style"], aoi=aoi)
        reporter.log(f"{len(pre) + len(post)} path(s) written to {list_path}, AOI to {aoi_path}")
        parallel_download(list_path, folder, reporter=reporter)

        paths = [_local_path(folder, p) for p in pre + post]
        missing = [p.name for p in paths if not p.is_dir()]
        if missing:
            raise FileNotFoundError(f"not in data/raw/{folder} after the download: {', '.join(missing)}")
        paths = [str(p) for p in paths]

        if mode == "GRD":
            result = main_preprocess_grd(paths[0], paths[1], str(aoi_path), name, reporter=reporter)
        else:
            result = main_preprocess(*paths, str(aoi_path), name, reporter=reporter)
        return {"pre": result["pre"], "post": result["post"], "raw": str(RAW_DIR / folder)}

    kind = "backscatter" if mode == "GRD" else "backscatter + coherence"
    return jobs.start(f"pre/post {kind} -> {name}", run,
                      log_path=PRE_POST_DIR / name / f"{name}.log")


ROUTES = {
    "GET": {},
    "POST": {"/api/pre_post/check": api_check, "/api/pre_post/run": api_run},
}
