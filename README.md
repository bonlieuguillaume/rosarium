# rosarium

Preprocessing toolbox for Sentinel-1 data. Automates end-to-end pipelines, from
tile search and download to analysis-ready outputs.

Every step is a *feature*: a folder of `features/` holding a Python module (the
logic, usable as a library), a notebook and a README explaining the method.
The features that need a map or a terminal are reachable through two front
doors: `rosarium.py` (command line) and the webmap in `frontend/` (browser).

## Features

The chain, end to end: draw an area of interest, list the products covering
it, download them, preprocess them into analysis-ready GeoTIFFs.

| Feature | What it does |
| --- | --- |
| [`aoi_to_slc`](features/aoi_to_slc/README.md) | From an area of interest to the Sentinel-1 SLC / GRD products covering it: search the CDSE STAC catalogue, tick products on a map, write their S3 paths to a text file. No credentials for the search. |
| [`download_products`](features/download_products/README.md) | That path file, downloaded: one parallel `rclone copy` from CDSE, then every `.SAFE` flattened into `data/raw/<folder>/`; products already there are skipped. |
| [`pre_post/pre_post_backscatter_coherence`](features/pre_post/pre_post_backscatter_coherence/README.md) | Four SLC products (2 before an event, 2 after) → `<name>_pre.tif` and `<name>_post.tif`, bands `gamma0_VH`, `gamma0_VV`, `coh_VH`, `coh_VV`. |
| [`pre_post/pre_post_backscatter`](features/pre_post/pre_post_backscatter/README.md) | Two GRD products (1 before, 1 after) → the same two files with the two `gamma0` bands. No coherence: GRD is detected. |
| [`snap_gpt`](features/snap_gpt/README.md) | The SNAP graphs the two pipelines run, each reachable on its own: backscatter, coherence, gathering, backscatter-grd, and the mosaic of per-sub-swath tiles. |
| [`polygon_to_swaths_bursts`](features/polygon_to_swaths_bursts/README.md) | Which sub-swaths and bursts of a Sentinel-1 SLC product a polygon intersects, read from the annotation XML without opening the image data. |

## Layout

```
rosarium/
├── rosarium.py            entry point: python rosarium.py <command> [options]
├── env_light_rosarium.yml the conda env
├── launch/                double-click launchers of the webmap + desktop shortcut makers
│   ├── rosarium.bat       Windows launcher        make_shortcut.bat  desktop .lnk
│   ├── rosarium.sh        Linux/macOS launcher    make_shortcut.sh   .desktop entry (Linux)
│   └── rosarium.command   macOS double-click, hands over to rosarium.sh  / .app bundle (macOS)
├── features/              one folder per feature: module + notebook + README
│   ├── aoi_to_slc/
│   ├── download_products/
│   ├── pre_post/          the two pre/post pipelines
│   │   ├── pre_post_backscatter/             (GRD)
│   │   └── pre_post_backscatter_coherence/   (SLC)
│   ├── snap_gpt/          the SNAP graphs + their runner (graphs/*.xml)
│   └── polygon_to_swaths_bursts/
├── frontend/              the webmap, shared by the features shown on the map
│   ├── server.py          local HTTP server: static page + JSON routes
│   ├── jobs.py            the one background job (download, pre/post run) and its progress
│   ├── api/               one module of JSON routes per feature
│   └── static/            index.html, style.css, map.js + job.js (shared), <feature>.js
└── data/                  never versioned (only the folders are)
    ├── raw/<folder>/      Sentinel-1 SLC / GRD products, as downloaded
    ├── preprocessed/      processing outputs
    │   └── pre_post/      <name>/ per run, temp/ intermediates, default/ unnamed runs
    └── utils/             intermediates: path files (list.txt), AOIs (.geojson)
```

The `.lnk`, `.desktop` entry and `.app` bundle listed next to `make_shortcut` are
not in the repository: they are the shortcuts that script creates on the desktop.

## Setup

A conda env named `rosarium` (miniforge / mamba), Python 3.11, every package
from conda-forge — `env_light_rosarium.yml` at the root:

```
mamba env create -f env_light_rosarium.yml       # first time
mamba env update -n rosarium -f env_light_rosarium.yml
```

Two things the env cannot do on its own:

- **SNAP** (≥ 10), for the pre/post pipelines, is installed separately —
  <https://step.esa.int/main/download/snap-download/>. Only its `gpt` runner is
  used; it is looked up in `SNAP_GPT`, on the PATH, then in the usual install
  folders, and `--gpt PATH` overrides that.
- **rclone** comes with the env, but its CDSE remote is configured once, with
  your own S3 keys — see the README of
  [`download_products`](features/download_products/README.md).

## Usage

Everything runs from the repository root, in the `rosarium` env.

**Webmap** — the whole chain from the browser:

```
python rosarium.py webmap --open
python rosarium.py webmap --port 9000 --path-file C:/data/list.txt --days 60
```

The page opens on <http://localhost:8050>, with two tabs sharing one AOI. The
AOI's area shows in km², while drawing and under the AOI box; the ruler under
the drawing tools measures a distance (click the points, click the last one to
finish, Esc to cancel; click the measured line to remove it).

- **Pre / post** (the main one): choose *Backscatter* (2 GRD) or *Backscatter
  + coherence* (4 SLC), draw the AOI, give a date range before and after the
  event, search; the two sides show in two colours, and once a product is
  picked only those that can go with it stay (same relative orbit; for SLC,
  same framing too — see the
  [pipeline's README](features/pre_post/pre_post_backscatter_coherence/README.md)).
  *Download + preprocess* downloads the products into `data/raw/<raw folder>/`
  and runs the pipeline into `data/preprocessed/pre_post/<name>/` — both
  `vrac` when left empty, the name following the raw folder by default. A
  panel on the map follows the run: total time, each step with its duration,
  rclone's statistics, the sub-swaths and bursts kept (drawn on the map), the
  graph in progress with its percentage, the log (also saved as
  `<name>/<name>.log`), and a *Cancel* button. *Download only* and *Write S3
  paths* are there too.
- **Browse & download**: search one product type over one date range, tick
  any products, *Download* them into `data/raw/<folder>/`, or only write
  their S3 paths (`data/utils/list.txt` by default, with the AOI next to it).

One job runs at a time. The server stops on its own a few seconds after the
last page is closed (reloading the page does not stop it, and picks up the
running job), and **stops the running job with it**; `--stay` keeps it
running until Ctrl+C. The page warns when SNAP's `gpt` or rclone is not
found.

**Desktop icon** — the launchers in `launch/` activate the env and run the
command above; the console window they open is the server, and it closes with
the page.

- Windows: double-click `launch/rosarium.bat`. For a desktop shortcut,
  double-click `launch/make_shortcut.bat` once: it writes `rosarium.lnk` on the
  desktop, with `launch/rosarium.ico` as icon when that file exists.
- Linux: `bash launch/rosarium.sh`. For a menu + desktop entry,
  `bash launch/make_shortcut.sh` once: it writes `Rosarium.desktop` in
  `~/.local/share/applications` and on the desktop, with `launch/rosarium.svg`
  as icon (`launch/rosarium.png` as fallback).
- macOS: double-click `launch/rosarium.command` (or `bash launch/rosarium.sh`).
  For a desktop app, `bash launch/make_shortcut.sh` once: it builds
  `~/Desktop/Rosarium.app`, which opens the launcher in the Terminal, with the
  icon made from `launch/rosarium.png` by the system tools (`sips`, `iconutil`).

The `.sh` / `.command` files may need `chmod +x launch/*.sh launch/*.command`
once after a clone from Windows (git there does not store the executable bit);
`bash launch/make_shortcut.sh` does it for you.

To rename the shortcut or change the logo, edit `SHORTCUT_NAME` / replace the
icon files in `launch/` (`rosarium.ico` for Windows, `rosarium.svg` and
`rosarium.png` for Linux and macOS), and run the `make_shortcut` script again.

**Command line** — `python rosarium.py --help` lists the commands,
`python rosarium.py <command> --help` the options of one. A command can be a
group whose second word picks the tool: `pre_post` holds the two pipelines of
`features/pre_post/`, named after their folders, and
`python rosarium.py pre_post` lists them. The whole chain, from the path file
the webmap wrote to the final GeoTIFFs:

```
python rosarium.py download --folder zta1

python rosarium.py pre_post backscatter_coherence \
    --pre1 A.SAFE --pre2 B.SAFE --post1 C.SAFE --post2 D.SAFE \
    --aoi data/utils/list_aoi.geojson --output zta1_slc
python rosarium.py pre_post backscatter --pre A.SAFE --post B.SAFE \
    --aoi data/utils/list_aoi.geojson --output zta1_grd

python rosarium.py gpt coherence --input1 A.SAFE --input2 B.SAFE --aoi aoi.geojson --pair pre
python rosarium.py bursts --slc-path product.zip --polygon aoi.geojson --coarse
```

The products land in `data/preprocessed/pre_post/<name>/` (`vrac` without
`--output`). The two pipelines
write there the same two file names, so name the run for what it holds. The
four memory flags of every SNAP command (`--xmx`, `--cache`, `--threads`,
`--tile-size`) are explained in the [`snap_gpt`](features/snap_gpt/README.md)
README; the defaults suit a 32 GB machine.

**Notebooks** — each feature folder has one (except `snap_gpt` and
`download_products`, which are commands); open it from that folder (it imports
the module sitting next to it) and fill the parameters cell.

**Python** — from the repository root:

```python
from features.aoi_to_slc.aoi_to_slc import search_products, write_path_file
from features.polygon_to_swaths_bursts.polygon_to_swaths_bursts import get_intersecting_bursts
from features.pre_post.pre_post_backscatter_coherence.pre_post_backscatter_coherence import main_preprocess
from features.pre_post.pre_post_backscatter.pre_post_backscatter import main_preprocess_grd
```

## Adding a feature

1. A folder in `features/<name>/` with `<name>.py`, its notebook and a README
   (a feature that is only a command can skip the notebook).
2. Terminal use: give the module a `main(argv=None, prog=None)` and add one
   entry to `COMMANDS` in `rosarium.py`. Features grouped in a folder, like
   `features/pre_post/`, go into a group entry instead (a dict of
   sub-commands): `python rosarium.py <group> <sub-command>`.
3. Map use: a `frontend/api/<name>.py` exposing `ROUTES` (functions taking
   `(body, config)`), listed in `FEATURES` of `frontend/server.py`; a
   `frontend/static/<name>.js` loaded after `map.js` and `job.js`, whose
   sidebar sections go in `index.html`, in a tab or in its own
   (`data-tab="<tab>"`). `map.js` owns the map, the AOI, the status line, the
   tabs and `api()`; a feature script hooks into `clearHooks`, `initHooks` and
   `tabHooks`. Anything long (download, SNAP) runs as a job:
   `jobs.start(title, fn)` in the route, `followJob(...)` in the page, and the
   feature function takes an optional `reporter` for its progress (contract in
   `features/snap_gpt/snap_gpt.py`).
