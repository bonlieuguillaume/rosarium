# pre_post_backscatter_coherence

From four Sentinel-1 **SLC** products — two before an event, two after — to two
analysis-ready GeoTIFFs over an area of interest: `<name>_pre.tif` and
`<name>_post.tif`, each with four bands, `gamma0_VH`, `gamma0_VV`, `coh_VH`,
`coh_VV`, on the same 10 m grid. Backscatter says what the ground looks like on
each side of the event; coherence, computed on each pair, says how stable it
stayed over the twelve days of the pair. The graphs themselves live in
[`features/snap_gpt/`](../../snap_gpt/README.md); this feature orders them.

- `pre_post_backscatter_coherence.py` — `main_preprocess(pre1, pre2, post1,
  post2, aoi, output_name, ...)` and the command line
  `python rosarium.py slc`.
- `pre_post_backscatter_coherence.ipynb` — a driver of the module: fill the
  parameters cell, run. Not a mirror.

## The four images

| Role | Position | Used for |
| --- | --- | --- |
| `pre1` | earliest | reference of the pre-event coherence pair |
| `pre2` | second | secondary of the pre pair; **master of the backscatter stack** |
| `post1` | third | slave of the backscatter stack; reference of the post pair |
| `post2` | latest | secondary of the post pair |

The four must come from the same relative orbit (same track, same viewing
geometry) so that they coregister; consecutive passes, 12 days apart with one
satellite, are the usual choice. `pre2` and `post1` bracket the event as
tightly as possible.

## Steps

1. **Sub-swaths and bursts** — which `IWx` and burst range the AOI needs, read
   from `pre2`'s annotation by `polygon_to_swaths_bursts` in coarse mode (a
   missing burst leaves a silent hole; an extra one costs seconds).
2. **Backscatter** — `pre2` × `post1` through `backscatter.xml`: calibrated,
   debursted, coregistered by cross-correlation, speckle-filtered,
   terrain-corrected, clipped. One `.dim` stack, master bands first.
3. **Pre-event coherence** — `pre1` × `pre2` through `coherence.xml`
   (`coherence_one_burst.xml` when a sub-swath keeps a single burst).
4. **Post-event coherence** — `post1` × `post2`, same graph.
5. **Gathering** — `gathering.xml` collocates the three products and splits
   the bands into the pre and the post GeoTIFF; the Python renames the bands
   and declares nodata 0.
6. **Mosaic** — only when the AOI spans several sub-swaths: steps 2–5 ran once
   per sub-swath, producing `<name>_IWx_pre.tif` tiles, which `run_mosaic`
   merges into `<name>_pre.tif` (the tiles are kept next to it).

Intermediate `.dim` products go to `data/preprocessed/pre_post/temp/` and are
overwritten by the next run; the final GeoTIFFs to
`data/preprocessed/pre_post/<name>/`, or to the folder given as a path prefix
(`--output data/preprocessed/pre_post/zta6/zta6_slc` →
`zta6/zta6_slc_pre.tif`). Name the run so that it says what it holds — this
folder is shared with the GRD pipeline, whose products only have the two
gamma0 bands.

A four-image run on a few-bursts AOI takes 10–30 minutes on a 32 GB machine;
the coherence graphs (Back-Geocoding + ESD) dominate. The memory flags
(`--xmx`, `--cache`, `--threads`, `--tile-size`) are explained in the
`snap_gpt` README.

## Usage

```
python rosarium.py slc \
    --pre1  data/raw/vrac/S1A_IW_SLC__1SDV_20260804T..._A.SAFE \
    --pre2  data/raw/vrac/S1A_IW_SLC__1SDV_20260816T..._B.SAFE \
    --post1 data/raw/vrac/S1A_IW_SLC__1SDV_20260828T..._C.SAFE \
    --post2 data/raw/vrac/S1A_IW_SLC__1SDV_20260909T..._D.SAFE \
    --aoi data/utils/list_aoi.geojson --output zta1

python rosarium.py slc ... --xmx 10G --cache 3G --threads 4   # 16 GB laptop
python rosarium.py slc --help
```

`--aoi` takes an inline WKT polygon (quoted) or a WKT / GeoJSON file in lon/lat
WGS84 — the `<name>_aoi.geojson` the webmap writes next to the path file. The
products may be `.SAFE` folders or the `.zip` archives.

From Python (repository root on `sys.path`, or from this folder as the
notebook does):

```python
from features.pre_post.pre_post_backscatter_coherence.pre_post_backscatter_coherence import main_preprocess
from features.snap_gpt.snap_gpt import GptOptions

paths = main_preprocess(pre1, pre2, post1, post2, aoi="aoi.geojson", output_name="zta1",
                        gpt_options=GptOptions(xmx="16G", cache="4G", threads=8))
paths["pre"], paths["post"]
```

Each step is also reachable alone through `python rosarium.py gpt <step>`,
which is the way to resume a run that failed half-way: the `.dim` of the
completed steps are still in `temp/`.

## Dependencies

Those of `snap_gpt`: SNAP installed, `gdal`, `scipy`, `numpy`, `geopandas`,
`shapely` from conda-forge.
