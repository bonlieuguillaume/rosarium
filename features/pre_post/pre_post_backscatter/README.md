# pre_post_backscatter

From two Sentinel-1 **GRD** products — one before an event, one after — to two
analysis-ready GeoTIFFs over an area of interest: `<name>_pre.tif` and
`<name>_post.tif`, each with `gamma0_VH` and `gamma0_VV` on the same 10 m grid.
The GRD counterpart of
[`pre_post_backscatter_coherence`](../pre_post_backscatter_coherence/README.md):
no coherence (GRD products are detected — the phase is gone), but two images
instead of four and a single graph. The graph itself lives in
[`features/snap_gpt/`](../../snap_gpt/README.md).

- `pre_post_backscatter.py` — `main_preprocess_grd(pre, post, aoi,
  output_name, ...)` and the command line `python rosarium.py pre_post backscatter`.
- `pre_post_backscatter.ipynb` — a driver of the module: fill the parameters
  cell, run. Not a mirror.

## Steps

1. **One graph, both images** — `backscatter_grd.xml`: Apply-Orbit-File →
   ThermalNoiseRemoval → Remove-GRD-Border-Noise → Calibration → CreateStack →
   Cross-Correlation → Warp → Speckle-Filter → Terrain-Correction → Subset.
   The two images are coregistered so that they share a pixel grid, `pre` being
   the master.
2. **Split** — the resulting `.dim` stack is cut by GDAL into the master bands
   (`<name>_pre.tif`) and the slave bands (`<name>_post.tif`), the bands renamed
   and nodata 0 declared.

There is no sub-swath or burst detection: a GRD product is already debursted
and covers the full swath, so `TOPSAR-Split` does not apply and no mosaic is
needed. The AOI is only the clip, applied after terrain correction.

The two products must still come from the **same relative orbit**: CreateStack
and Cross-Correlation coregister them in radar geometry, before terrain
correction, which only makes sense for one viewing geometry. Their framing may
differ — GRD slices of one track shift by tens of kilometres from date to
date — as long as each covers the whole AOI.

The intermediate `backscatter_grd.dim` goes to
`data/preprocessed/pre_post/temp/` and is overwritten by the next run; the
final GeoTIFFs to `data/preprocessed/pre_post/<name>/` (`vrac` when no name is
given, like the default download folder), or to the folder given as a path
prefix (`--output data/preprocessed/pre_post/zta6/zta6_grd` →
`zta6/zta6_grd_pre.tif`). Name the run so that it says what it holds — this
folder is shared with the SLC pipeline, whose products also carry the two
coherence bands.

**GRD from `aoi_to_slc` are the COG variant** (`..._COG.SAFE`), the only one in
the CDSE STAC: same values and same annotation as the original GRD, readable by
SNAP from version 10.

## Usage

**Webmap** (`python rosarium.py webmap --open`, tab *Pre / post*, mode
*Backscatter*): draw the AOI, give a pre and a post date range, search, pick
one product on each side — once one is picked, only the other side's products
of the same relative orbit stay on the map — then *Download + preprocess*.
The page downloads the two products into `data/raw/<raw folder>/` (skipped if
already there), runs the pipeline into `data/preprocessed/pre_post/<name>/`,
and follows it: download statistics, the graph's percentage, the log (also
written to `<name>/<name>.log`).

**Command line**:

```
python rosarium.py pre_post backscatter \
    --pre  data/raw/vrac/S1A_IW_GRDH_1SDV_20260816T..._COG.SAFE \
    --post data/raw/vrac/S1A_IW_GRDH_1SDV_20260828T..._COG.SAFE \
    --aoi data/utils/list_aoi.geojson --output zta1_grd

python rosarium.py pre_post backscatter ... --xmx 10G --cache 3G --threads 4   # 16 GB laptop
python rosarium.py pre_post backscatter --help
```

`--aoi` takes an inline WKT polygon (quoted) or a WKT / GeoJSON file in lon/lat
WGS84 — the `<name>_aoi.geojson` the webmap writes next to the path file. The
products may be `.SAFE` folders or the `.zip` archives. The memory flags are
explained in the `snap_gpt` README.

From Python (repository root on `sys.path`, or from this folder as the
notebook does):

```python
from features.pre_post.pre_post_backscatter.pre_post_backscatter import main_preprocess_grd

paths = main_preprocess_grd(pre, post, aoi="aoi.geojson", output_name="zta1_grd")
paths["pre"], paths["post"]
```

The same graph is reachable alone through
`python rosarium.py gpt backscatter-grd`, which takes the same arguments.

## Dependencies

Those of `snap_gpt`: SNAP installed, `gdal`, `numpy`, `geopandas`, `shapely`
from conda-forge (`scipy` only for the SLC mosaic).
