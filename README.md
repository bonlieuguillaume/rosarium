# rosarium

Preprocessing toolbox for Sentinel-1 data. Automates end-to-end pipelines, from
tile search and download to analysis-ready outputs.

Every step is a *feature*: a folder of `features/` holding a Python module (the
logic, usable as a library), a notebook and a README explaining the method.
The features that need a map or a terminal are reachable through two front
doors: `rosarium.py` (command line) and the webmap in `frontend/` (browser).

## Features

| Feature | What it does |
| --- | --- |
| [`aoi_to_slc`](features/aoi_to_slc/README.md) | From an area of interest to the Sentinel-1 SLC / GRD products covering it: search the CDSE STAC catalogue, tick products on a map, write their S3 paths to a text file for a downloader. No download, no credentials. |
| [`polygon_to_swaths_bursts`](features/polygon_to_swaths_bursts/README.md) | Which sub-swaths and bursts of a Sentinel-1 SLC product a polygon intersects, read from the annotation XML without opening the image data. |

## Layout

```
rosarium/
├── rosarium.py            entry point: python rosarium.py <command> [options]
├── rosarium.bat           double-click launcher of the webmap (Windows)
├── features/              one folder per feature: module + notebook + README
│   ├── aoi_to_slc/
│   └── polygon_to_swaths_bursts/
├── frontend/              the webmap, shared by the features shown on the map
│   ├── server.py          local HTTP server: static page + JSON routes
│   ├── api/               one module of JSON routes per feature
│   └── static/            index.html, style.css, map.js (shared), <feature>.js
└── data/                  never versioned (only the folders are)
    ├── raw/               Sentinel-1 SLC / GRD products
    ├── preprocessed/      processing outputs
    └── utils/             intermediates: path files (.txt), AOIs (.geojson)
```

## Setup

A conda env named `rosarium` (miniforge / mamba), Python 3.11, every package
from conda-forge:

```
ipykernel geopandas shapely numpy folium requests ipyleaflet ipywidgets
```

The env is created and updated from its yml by hand, outside this repository.

## Usage

Everything runs from the repository root, in the `rosarium` env.

**Webmap** — draw an AOI, list the Sentinel-1 products covering it, write the
path file:

```
python rosarium.py webmap --open
python rosarium.py webmap --port 9000 --path-file C:/data/list.txt --days 60
```

The page opens on <http://localhost:8050>; Ctrl+C in the terminal stops it.
Path files land in `data/utils/` by default, with the AOI next to them.

**Desktop icon** — `rosarium.bat` activates the env and runs the command above.
Right-click it → *Send to* → *Desktop (create shortcut)*; the shortcut's icon
can be changed in its properties. The console window it opens is the server:
keep it open while using the map, close it to stop.

**Command line** — `python rosarium.py --help` lists the commands,
`python rosarium.py <command> --help` the options of one:

```
python rosarium.py bursts --slc-path product.zip --polygon aoi.geojson --coarse
```

**Notebooks** — each feature folder has one; open it from that folder (it
imports the module sitting next to it) and fill the parameters cell.

**Python** — from the repository root:

```python
from features.aoi_to_slc.aoi_to_slc import search_products, write_path_file
from features.polygon_to_swaths_bursts.polygon_to_swaths_bursts import get_intersecting_bursts
```

## Adding a feature

1. A folder in `features/<name>/` with `<name>.py`, its notebook and a README.
2. Terminal use: give the module a `main(argv=None, prog=None)` and add one
   entry to `COMMANDS` in `rosarium.py`.
3. Map use: a `frontend/api/<name>.py` exposing `ROUTES` (functions taking
   `(body, config)`), listed in `FEATURES` of `frontend/server.py`; a
   `frontend/static/<name>.js` loaded after `map.js`, whose sidebar sections go
   in `index.html`. `map.js` owns the map, the AOI, the status line and
   `api()`; a feature script hooks into `clearHooks` and `initHooks`.
