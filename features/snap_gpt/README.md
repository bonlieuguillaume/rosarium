# snap_gpt

The SNAP graphs that turn raw Sentinel-1 products into terrain-corrected
GeoTIFFs, and the Python that runs them. This is the engine of the two
pre/post pipelines of [`features/pre_post/`](../pre_post/); it has no notebook
of its own — the pipelines are the user-facing side.

- `snap_gpt.py` — one function per graph (`run_backscatter`, `run_coherence`,
  `run_gathering`, `run_backscatter_grd`), the mosaic of per-sub-swath
  GeoTIFFs (`run_mosaic`, no SNAP involved), the `gpt` wrapper and its
  memory/performance settings (`GptOptions`), and a command line that exposes
  each step alone: `python rosarium.py gpt <step>`.
- `graphs/` — the five graph XMLs, parameterised with `${...}` placeholders
  that the Python side fills in (`-P` flags of `gpt`).

| Graph | Input | Chain | Output |
| --- | --- | --- | --- |
| `backscatter.xml` | 2 SLC | Apply-Orbit-File → TOPSAR-Split → ThermalNoiseRemoval → Calibration → TOPSAR-Deburst → CreateStack → Cross-Correlation → Warp → Speckle-Filter → Terrain-Correction → Subset | gamma0 stack, master then slave bands (`.dim`) |
| `coherence.xml` | 2 SLC | Apply-Orbit-File → TOPSAR-Split → Back-Geocoding → Enhanced-Spectral-Diversity → Coherence → TOPSAR-Deburst → Terrain-Correction → Subset | coherence (`.dim`) |
| `coherence_one_burst.xml` | 2 SLC | same without Enhanced-Spectral-Diversity | used when a sub-swath keeps a single burst: ESD estimates from the overlap between consecutive bursts and yields an empty product with one. **Keep the two graphs in sync.** |
| `gathering.xml` | 3 `.dim` | 3× Read → Collocate → BandSelect → Write (pre) / BandSelect → Write (post) | `<name>_pre.tif`, `<name>_post.tif` |
| `backscatter_grd.xml` | 2 GRD | Apply-Orbit-File → ThermalNoiseRemoval → Remove-GRD-Border-Noise → Calibration → CreateStack → Cross-Correlation → Warp → Speckle-Filter → Terrain-Correction → Subset | gamma0 stack (`.dim`), split into two GeoTIFFs by GDAL |

## SNAP: the external dependency

SNAP is not a conda package: install it from
<https://step.esa.int/main/download/snap-download/> (SNAP 10 or later, so that
the COG variant of GRD products that `aoi_to_slc` lists can be read). Only its
`gpt` command-line runner is used, in a sub-process, never the GUI.

`find_gpt()` locates it, in this order: the `SNAP_GPT` environment variable,
`gpt` on the PATH, then the usual install folders — `C:\Program Files\esa-snap`
on Windows, `~/esa-snap` and `/opt/esa-snap` on Linux, `/Applications/esa-snap`
on macOS (plus the `snap` spelling of each). `--gpt PATH` overrides all of
that on every command line, and the `--help` of each command prints what was
found.

The Copernicus 30 m DEM the graphs use is downloaded by SNAP on first use into
`~/.snap/auxdata/dem/`; so are the orbit files. The first run of a new area
therefore needs the network and takes longer.

## Where the outputs go

Everything lands under `data/preprocessed/pre_post/`:

| Path | Content |
| --- | --- |
| `temp/` | the intermediate `.dim` products of a pipeline run (`backscatter[_IWx].dim`, `coherence_pre[_IWx].dim`, `coherence_post[_IWx].dim`, `backscatter_grd.dim`) — overwritten by the next run |
| `<name>/` | the final GeoTIFFs of a run: `<name>_pre.tif`, `<name>_post.tif`, plus the per-sub-swath tiles `<name>_IWx_pre.tif` when the AOI spans several; `<name>.log` for a run started from the webmap. A pipeline given no name writes to `vrac/` |
| `default/` | single steps run without a name (`coherence.dim` of a standalone coherence, `pre.tif` / `post.tif` of a gathering or GRD step without `--output`) |

The `--output` argument of the gathering / GRD steps and of the pipelines
takes either a simple name (→ `<name>/`) or a path prefix
(`data/preprocessed/pre_post/zta6/zta6_slc` → `zta6/zta6_slc_pre.tif`): the
caller then controls the folder.

## Following a run: the reporter

Every `run_*` function and both pipelines take an optional `reporter`. Left
to `None` — the command line — gpt writes straight to the console, as always.
The webmap passes one (`frontend/jobs.py`) that receives the step list, marks
each step as it starts, runs gpt itself to read its `....10%....20%` progress
from the pipe, and can kill it on cancel. Any object with `plan`, `step`,
`info`, `log` and `run` will do — the contract is in the module docstring.
Step names come from `step_name()` (`backscatter IW2`, `coherence pre IW2`,
`gathering IW2`, `mosaic`, `backscatter_grd`...), so that what a pipeline
announces matches what the functions mark.

## GPT memory & performance

Every command takes four flags that are passed to each `gpt` call and
**override SNAP's own configuration** (`gpt.vmoptions`, `snap.properties`; the
GUI settings do not apply). Defaults suit a 32 GB / 8-core machine; the full
rationale is in the comment block at the top of `snap_gpt.py`.

| Flag | Default | What it is | How to choose |
| --- | --- | --- | --- |
| `--xmx` | `21G` | Java heap ceiling: cache **and** operator working memory must fit under it | ~2/3 of the RAM. Too low → Java OutOfMemoryError on large AOIs; too high → the machine swaps and gpt dies with an `hs_err_pid*.log` |
| `--cache` | `8192M` | tile cache, *inside* the heap; keeps computed tiles so they are not recomputed | 1/4–1/3 of `--xmx`. Too small only costs time, never crashes; too big fills up and starves the operators. First lever on large AOIs |
| `--threads` | `16` | tiles computed in parallel | ≤ hardware threads; the number of physical cores when memory is tight (SNAP scales poorly beyond ~8). Second lever |
| `--tile-size` | `512` | edge of the square tiles, pixels | a power of two (256/512/1024), to match the block size of files on disk and of pyramid levels. Leave at 512 unless you know why |

Example, a large AOI on a 16 GB laptop:
`python rosarium.py pre_post backscatter_coherence ... --xmx 10G --cache 3G --threads 4`.

## Conventions the graphs and the Python share

**Sub-swaths and bursts.** The SLC graphs process one sub-swath at a time
(`TOPSAR-Split`), on the burst range the AOI needs. That range comes from the
[`polygon_to_swaths_bursts`](../polygon_to_swaths_bursts/README.md) feature
through the wrapper `snap_gpt.polygon_to_swaths_bursts`, which runs it in
**coarse mode** (footprints dilated by 2 km before the test): a missing burst
leaves a silent nodata hole in the final GeoTIFF, an extra one costs seconds.
When the AOI spans several sub-swaths the graph runs once per sub-swath, the
outputs get an `_IWx` suffix, and `run_mosaic` merges the tiles at the end.

**Output grid.** Every `Terrain-Correction` node runs with
`alignToStandardGrid=true` (origin 0,0): the 10 m output grid is snapped so that
pixel edges fall on multiples of 10 m of the UTM easting/northing, instead of
starting at each product's own bounding-box corner. Terrain-Correction
interpolates exactly once either way — this only fixes *where* the grid is laid
— but all products of the same UTM zone (sub-swaths, dates, backscatter vs
coherence) then share one grid: `run_mosaic` and `Collocate` copy pixels instead
of resampling them, and it is the same grid as GDAL's `-tap` or Sentinel-2's
10 m tiles.

**UTM zone: chosen per run, not per pipeline.** The zone comes from
`mapProjection = AUTO:42001`: SNAP picks it from the centre of the product
entering Terrain-Correction, **independently for every graph run**. In the SLC
pipeline that is one run per sub-swath and per step (backscatter, coherence
pre, coherence post), so they need not all land in the same zone — an AOI near
a zone boundary (every 6° of longitude) is enough for the sub-swaths, ~250 km
apart across the track, to fall on both sides. Nothing fails when that
happens; the mismatch is absorbed downstream, at the cost of **one extra
nearest-neighbour reprojection** of the data from the other zone:

| Mismatch | Absorbed by | Consequence |
| --- | --- | --- |
| two sub-swaths in different zones | `run_mosaic` warps every tile onto the grid of the **first** input (IW1 when it is used) | the tiles of the other zone are resampled once more; the final GeoTIFF is in the first tile's zone, which is not necessarily the AOI's |
| backscatter and coherence of one sub-swath in different zones | `Collocate` (gathering) resamples the coherence onto the backscatter grid, the reference | the coherence bands are resampled once more; gamma0 is never touched |

That extra reprojection keeps every value as it was (nearest neighbour copies,
it never blends), but moves pixels by up to half a pixel and, where the two
rotated grids meet at a cell boundary, picks a few isolated source pixels twice
and their neighbour not at all. A loss of rigour, not a visible defect. What
stays guaranteed: the `_pre.tif` and `_post.tif` of one run always share one
zone and one grid (same Collocate, then two mosaics over the tiles in the same
order), so a pre/post comparison is never misaligned. Across **separate** runs —
a GRD and an SLC run of the same AOI, or two AOIs — nothing reconciles the
zones: their outputs may be in different projections and need a reprojection
before a pixel-by-pixel comparison. The GRD pipeline has a single
Terrain-Correction, hence a single zone per run.

Possible improvement, not done: compute the zone once from the AOI centroid and
pass it to the four Terrain-Correction nodes as a graph parameter instead of
`AUTO:42001`. Every tile would then be terrain-corrected straight onto the
final grid, and every run of an AOI would share its projection.

**Band naming & master/slave.** SNAP band names (dates, `_mst`/`_slv`,
sub-swath) are unreliable, so the Python never parses them: it relies on the
**order of the sources** in the graphs (first source = master, bands written
first) and on Collocate suffixes (`_M`, `_S0`, `_S1`) that are *predicted* by
`_resolve_gathering_bands`. These couplings are documented **directly inside the
graph files** — the header comment of `graphs/gathering.xml` and the comments on
the `CreateStack` / `Back-Geocoding` nodes of `backscatter.xml`,
`backscatter_grd.xml` and `coherence.xml`. Read them before editing a graph or
re-saving it from SNAP's Graph Builder. The final bands are renamed to
`gamma0_VH`, `gamma0_VV`, `coh_VH`, `coh_VV`.

**Nodata is 0.** SNAP fills masked pixels (sea, out of swath) with 0.0 and the
declaration is lost in the GeoTIFF conversion, so `_clean_geotiff` re-declares
0.0 as nodata on every band, and strips the flag bands Collocate appends. The
mosaic never writes NaN either: one invalid marker only, the one declared.

**Mosaic of sub-swaths.** Where two tiles overlap, each pixel is taken from the
tile in which it lies farthest from an invalid pixel (every band finite and
non-zero; distance transform from scipy). Adjacent sub-swaths overlap by 1–2 km
and each tile is degraded along its own swath edge (a 1-px NaN line in gamma0, a
wider zeroed fringe in coherence): a plain "last input wins" warp would paint
those fringes over the neighbour's good data. All inputs are warped on the union
grid first (nearest neighbour — a plain copy when the tiles share a UTM zone
thanks to the standard grid, one extra resampling of the other tiles when they
do not, see *UTM zone* above) and held in memory: N tiles × B bands × H × W
float32.

## Usage

```
python rosarium.py gpt --help                 # the steps
python rosarium.py gpt <step> --help          # the options of one step

python rosarium.py gpt backscatter --input1 pre2.SAFE --input2 post1.SAFE --aoi aoi.geojson
python rosarium.py gpt coherence   --input1 pre1.SAFE --input2 pre2.SAFE  --aoi aoi.geojson --pair pre
python rosarium.py gpt coherence   --input1 post1.SAFE --input2 post2.SAFE --aoi aoi.geojson --pair post
python rosarium.py gpt gathering   --input-backscatter data/preprocessed/pre_post/temp/backscatter.dim \
    --input-coh-pre data/preprocessed/pre_post/temp/coherence_pre.dim \
    --input-coh-post data/preprocessed/pre_post/temp/coherence_post.dim --output zta1
python rosarium.py gpt backscatter-grd --pre pre.SAFE --post post.SAFE --aoi aoi.geojson --output zta1
python rosarium.py gpt mosaic --inputs zta1_IW2_pre.tif zta1_IW3_pre.tif --output zta1_pre.tif
```

`--aoi` takes an inline WKT polygon (quoted) or a path to a WKT / GeoJSON file,
in lon/lat WGS84 — the `<name>_aoi.geojson` that the webmap writes next to the
path file works as is. The module also runs on its own with the same options:
`python features/snap_gpt/snap_gpt.py <step> ...`.

From Python (repository root on `sys.path`):

```python
from features.snap_gpt.snap_gpt import run_backscatter, run_coherence, run_gathering, run_mosaic, GptOptions

opts = GptOptions(xmx="10G", cache="3G", threads=4)
run_backscatter("pre2.SAFE", "post1.SAFE", "aoi.geojson", gpt_options=opts)
```

## Dependencies

conda-forge: `gdal` (the `osgeo` module: band cleanup, GRD split, mosaic),
`scipy` (distance transform of the mosaic), `numpy`, plus `geopandas` /
`shapely` through `polygon_to_swaths_bursts`. External: SNAP (above).
