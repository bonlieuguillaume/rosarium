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
| [`aoi_to_slc`](features/aoi_to_slc/README.md) | From an area of interest to the Sentinel-1 SLC / GRD products covering it: search the CDSE STAC catalogue, tick products on a map, write their S3 paths to a text file. No download, no credentials. |
| [`download_products`](features/download_products/README.md) | That path file, downloaded: one parallel `rclone copy` from CDSE, then every `.SAFE` flattened into `data/raw/<folder>/`. |
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
│   ├── api/               one module of JSON routes per feature
│   └── static/            index.html, style.css, map.js (shared), <feature>.js
└── data/                  never versioned (only the folders are)
    ├── raw/<folder>/      Sentinel-1 SLC / GRD products, as downloaded
    ├── preprocessed/      processing outputs
    │   └── pre_post/      <name>/ per run, temp/ intermediates, default/ unnamed runs
    └── utils/             intermediates: path files (list.txt), AOIs (.geojson)
```

## Setup

A conda env named `rosarium` (miniforge / mamba), Python 3.11, every package
from conda-forge — `env_light_rosarium.yml` at the root:

```
mamba env create -f env_light_rosarium.yml       # first time
mamba env update -n rosarium -f env_light_rosarium.yml
```

Two tools are not Python packages and are installed on the machine, each
needed by one feature only:

- **SNAP** (≥ 10) for the pre/post pipelines —
  <https://step.esa.int/main/download/snap-download/>. Only its `gpt` runner is
  used; it is looked up in `SNAP_GPT`, on the PATH, then in the usual install
  folders, and `--gpt PATH` overrides that.
- **rclone** for `download` — <https://rclone.org/install/>, plus one
  `rclone config` for the CDSE remote (see the feature's README).

## Usage

Everything runs from the repository root, in the `rosarium` env.

**Webmap** — draw an AOI, list the Sentinel-1 products covering it, write the
path file:

```
python rosarium.py webmap --open
python rosarium.py webmap --port 9000 --path-file C:/data/list.txt --days 60
```

The page opens on <http://localhost:8050>. The server stops on its own a few
seconds after the last page is closed (reloading the page does not stop it);
`--stay` keeps it running until Ctrl+C. Path files land in `data/utils/` by
default (`list.txt`), with the AOI next to them.

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
`python rosarium.py <command> --help` the options of one. The whole chain,
from the path file the webmap wrote to the final GeoTIFFs:

```
python rosarium.py download --folder zta1

python rosarium.py slc --pre1 A.SAFE --pre2 B.SAFE --post1 C.SAFE --post2 D.SAFE \
    --aoi data/utils/list_aoi.geojson --output zta1_slc
python rosarium.py grd --pre A.SAFE --post B.SAFE \
    --aoi data/utils/list_aoi.geojson --output zta1_grd

python rosarium.py gpt coherence --input1 A.SAFE --input2 B.SAFE --aoi aoi.geojson --pair pre
python rosarium.py bursts --slc-path product.zip --polygon aoi.geojson --coarse
```

The products land in `data/preprocessed/pre_post/<name>/`. `slc` and `grd`
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
   entry to `COMMANDS` in `rosarium.py`.
3. Map use: a `frontend/api/<name>.py` exposing `ROUTES` (functions taking
   `(body, config)`), listed in `FEATURES` of `frontend/server.py`; a
   `frontend/static/<name>.js` loaded after `map.js`, whose sidebar sections go
   in `index.html`. `map.js` owns the map, the AOI, the status line and
   `api()`; a feature script hooks into `clearHooks` and `initHooks`.
