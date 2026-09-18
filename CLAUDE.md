# rosarium

Preprocessing toolbox for Sentinel-1: one feature per folder of `features/`
(module + notebook + README), a single command-line entry point `rosarium.py`,
and one browser front-end `frontend/` that the map-based features plug into.
Read a feature's README before changing it; `README.md` at the root describes
the layout and how a feature is added.

## Environment

- conda/miniforge env named `rosarium`, Python 3.11, from
  `env_light_rosarium.yml` at the root. The user maintains that file and the
  env themselves: hand them the conda-forge package names to add, never edit or
  move the yml, never create or update the env.
- `launch/` holds the double-click launchers (`rosarium.bat`, `rosarium.sh`)
  and the desktop-shortcut makers (`make_shortcut.bat` → `.lnk`,
  `make_shortcut.sh` → `.desktop`); both look for the conda base in the usual
  home locations and for an optional logo (`rosarium.ico` / `.png`) next to
  them. Testing them must not touch the user's real desktop: write to the
  scratchpad instead.
- **Every dependency must be installable from conda-forge.** Hard requirement:
  no pip-only packages, no heavyweight SAR stacks (SNAP, ISCE, GAMMA) — the
  features reimplement what they need from product metadata.
- `frontend/server.py` is standard library only; the page loads Leaflet and the
  basemaps from the web.

## Working rules

- The user runs their own terminal, conda env and tests on real data. Do not
  install packages, modify the env, or run commands against their SAR products
  unless they explicitly ask. Ad-hoc checks belong in the scratchpad directory;
  a test server goes on a spare port and its outputs are removed afterwards.
- Windows. The "Miniforge Prompt" is `cmd.exe` (no `PS` in the prompt); VS
  Code's integrated terminal is PowerShell. Shell quoting differs between the
  two — matters when documenting CLI examples. Console output must stay ASCII
  (`->` not `→`): the cmd console is cp1252.
- **Never overwrite the user's own values.** Parameter cells hold what they
  chose: input paths, AOI, mode, output directories. `NotebookEdit` rewrites a
  whole cell, so editing one line of such a cell silently restores every other
  value from whenever the cell was last read — and the user's edits since are
  lost. Re-read the cell immediately before writing it, carry the current
  values across verbatim, and if a value cannot be confirmed, ask instead of
  guessing.
- `data/` holds the user's products (`raw/`), outputs (`preprocessed/`) and
  intermediates (`utils/`: path files, AOIs). Only the `.gitkeep` files are
  versioned; defaults point there (`data/utils/products.txt`).

## Conventions

- Code, comments and docstrings in English (numpy-style docstrings);
  conversation with the user in French.
- Features import each other, and the front-end imports them, through the
  repository root on `sys.path` (`from features.<name>.<name> import ...`);
  notebooks import the module next to them (`from <name> import ...`) and are
  run from their own folder.
- `polygon_to_swaths_bursts` exists as both a notebook and a module holding
  the same functions (the CLI part is module-only): any change to a function
  must be applied to the two in the same edit. `aoi_to_slc.ipynb` only drives
  its module — not a mirror.
- A notebook whose opening markdown cell describes what each cell does keeps
  that description in sync: adding, removing or reordering a cell means
  updating the table in the same edit. A stale walkthrough is worse than none.
- Command-line tools expose `main(argv=None, prog=None)`; `rosarium.py` passes
  `prog="python rosarium.py <command>"` so usage and examples match the call.
- Front-end: `frontend/api/<feature>.py` holds `ROUTES = {"GET": {}, "POST":
  {}}` of functions `(body, config) -> JSON-able`; `frontend/static/map.js`
  owns the map, the single AOI, the status line and `api()`; a feature's script
  registers in `clearHooks` / `initHooks` rather than redefining them. No
  framework, no build step.

## Maintaining this file

Keep it current as the repo evolves: add a line under Features for a new one,
and record a decision or constraint here once it is durable and not derivable
from the code. Be selective — this file is loaded into context every session,
so prefer rewriting an existing line over appending a new one, and drop what no
longer helps.

## Features

- `features/aoi_to_slc/` — webmap (browser through `frontend/`, or ipyleaflet +
  ipywidgets in the notebook): draw or paste an AOI, list the Sentinel-1
  SLC/GRD scenes covering it, tick some, write their S3 paths
  (`/eodata/Sentinel-1/SAR/.../<product>.SAFE`, the downloader's convention;
  `s3://` and bare-key forms optional) to a text file, one per line, plus the
  AOI as `<name>_aoi.geojson`. **No download here**: the user's downloader, in
  another repo, takes that file and an output folder. Search = CDSE STAC hit
  directly with `requests`, anonymous, no credentials. Not asf_search: no
  `eodata` paths there. **GRD = the COG variant**, the only one in the CDSE
  STAC — a distinct product from the original GRD (other checksum suffix,
  `IW_GRDH_1S-COG` folder), accepted deliberately since the user's chain runs
  SNAP 13 (COG readable from SNAP 10). If originals are ever needed, CDSE
  OData lists both with `S3Path`: rewrite `search_products` only.
- `features/polygon_to_swaths_bursts/` — Sentinel-1 SLC: which sub-swaths and
  bursts a polygon intersects, read from the product annotation XML without
  touching the image data. Module + CLI (`rosarium.py bursts`), a mirrored
  notebook, and a `README.md` detailing the algorithm, its accuracy limits and
  its usage. Not in the front-end yet: planned as the intermediate result of a
  later processing feature.
- Not ported from the `geo` repo: `asf/` (gamma0 RTC through ASF HyP3). Left
  out on purpose.
