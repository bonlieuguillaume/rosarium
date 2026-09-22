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
- `launch/` holds the double-click launchers (`rosarium.bat`, `rosarium.sh`,
  `rosarium.command` for the macOS Finder) and the desktop-shortcut makers
  (`make_shortcut.bat` → `.lnk`; `make_shortcut.sh` → `.desktop` on Linux,
  `.app` bundle on macOS via `sips`/`iconutil`). They look for the conda base
  in the usual locations and for the logo next to them (`rosarium.ico`,
  `.svg`, `.png`). Testing them must not touch the user's real desktop: fake
  `HOME` (and `uname` for the macOS branch) in the scratchpad instead. The
  macOS branch has never run on a Mac.
- **Every Python dependency must be installable from conda-forge**, no
  pip-only packages. Two external tools are installed on the machine, each
  used by one feature and called as a sub-process: SNAP (`gpt`, the pre/post
  pipelines) and rclone (`download`). Both are located at runtime, never
  hard-coded, and their feature's README says how to install them. Adding a
  third external dependency is a decision to bring to the user, not to take:
  ask whether it is worth it before writing code that needs it.
- `frontend/server.py` is standard library only; the page loads Leaflet and the
  basemaps from the web.

## Working rules

- The user runs their own terminal, conda env and tests on real data. Do not
  install packages, modify the env, or run commands against their SAR products
  unless they explicitly ask. Ad-hoc checks belong in the scratchpad directory;
  a test server goes on a spare port and its outputs are removed afterwards.
- Developed on Windows, **but every feature must run unchanged on Linux and
  macOS** — the repo gets cloned there. Rules: `pathlib` and `/`-agnostic
  paths, no hard-coded drive letters outside the users' parameter cells, no
  Windows-only modules or shell calls (`cmd`, PowerShell, `%VAR%`), no
  reliance on case-insensitive file names, and every OS-specific branch
  (`sys.platform`) gets its Unix side. Shell scripts stay POSIX-ish bash: no
  GNU-only flags (`readlink -f`, `sed -i` without suffix) — macOS ships BSD
  tools. Anything that launches or installs ships in every form in `launch/`
  (`.bat` + `.sh` + `.command`). Line endings are pinned by `.gitattributes`
  (`.sh`/`.command` LF, `.bat` CRLF): keep new file kinds in there.
- Windows shells: the "Miniforge Prompt" is `cmd.exe` (no `PS` in the prompt);
  VS Code's integrated terminal is PowerShell. Shell quoting differs between
  the two — matters when documenting CLI examples. Console output must stay
  ASCII (`->` not `→`): the cmd console is cp1252.
- **Never overwrite the user's own values.** Parameter cells hold what they
  chose: input paths, AOI, mode, output directories. `NotebookEdit` rewrites a
  whole cell, so editing one line of such a cell silently restores every other
  value from whenever the cell was last read — and the user's edits since are
  lost. Re-read the cell immediately before writing it, carry the current
  values across verbatim, and if a value cannot be confirmed, ask instead of
  guessing.
- `data/` holds the user's products (`raw/<folder>/`), outputs
  (`preprocessed/`) and intermediates (`utils/`: path files, AOIs). Only the
  `.gitkeep` files are versioned. The defaults chain the features together:
  `aoi_to_slc` and the webmap write `data/utils/list.txt`, which `download`
  reads and copies into `data/raw/vrac/`; the pre/post pipelines write
  `data/preprocessed/pre_post/<name>/`, with `temp/` for the `.dim`
  intermediates and `default/` for unnamed runs. Keep the two ends in sync
  when changing one.

## Conventions

- Code, comments and docstrings in English (numpy-style docstrings);
  conversation with the user in French.
- Features import each other, and the front-end imports them, through the
  repository root on `sys.path` (`from features.<name>.<name> import ...`);
  notebooks import the module next to them (`from <name> import ...`) and are
  run from their own folder.
- `polygon_to_swaths_bursts` exists as both a notebook and a module holding
  the same functions (the CLI part is module-only): any change to a function
  must be applied to the two in the same edit. The other notebooks
  (`aoi_to_slc`, the two `pre_post`) only drive their module — not mirrors.
  `snap_gpt` and `download_products` have no notebook: they are commands.
- A notebook whose opening markdown cell describes what each cell does keeps
  that description in sync: adding, removing or reordering a cell means
  updating the table in the same edit. A stale walkthrough is worse than none.
- Command-line tools expose `main(argv=None, prog=None)`; `rosarium.py` passes
  `prog="python rosarium.py <command>"` so usage and examples match the call.
- Front-end: `frontend/api/<feature>.py` holds `ROUTES = {"GET": {}, "POST":
  {}}` of functions `(body, config) -> JSON-able`; `frontend/static/map.js`
  owns the map, the single AOI, the status line, `api()` and the liveness
  heartbeat (`/api/ping` while open, `/api/bye` on `pagehide`; the server
  stops once no page is left, `--stay` disables it); a feature's script
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
- `features/download_products/` — the path file, downloaded: one parallel
  `rclone copy` from the CDSE `eodata` bucket, then every `.SAFE` flattened
  into `data/raw/<folder>/` (`vrac` by default). CLI only
  (`rosarium.py download`), standard library on the Python side. The user
  configures the `cdse:` remote themselves (`rclone config`).
- `features/snap_gpt/` — the SNAP graphs (`graphs/*.xml`) and their runner:
  one function per graph, `run_mosaic` (GDAL + scipy, no SNAP), the `gpt`
  wrapper and its memory options, exposed step by step as `rosarium.py gpt
  <step>`. No notebook. `gpt` is located by `find_gpt()`, never hard-coded.
  **The graphs and the Python are coupled**: band names are predicted, not
  parsed, from the source order in the XML and the Collocate suffixes — the
  contract is in the header comment of `gathering.xml` and the docstring of
  `_resolve_gathering_bands`. Read both before editing a graph.
  `coherence.xml` and `coherence_one_burst.xml` are the same graph minus
  Enhanced-Spectral-Diversity: change them together.
- `features/pre_post/` — the two pipelines that chain `snap_gpt`, both
  writing `<name>_pre.tif` / `<name>_post.tif` into
  `data/preprocessed/pre_post/<name>/`: `pre_post_backscatter_coherence/`
  (4 SLC -> gamma0 + coherence, `rosarium.py slc`, `main_preprocess`) and
  `pre_post_backscatter/` (2 GRD -> gamma0 only, `rosarium.py grd`,
  `main_preprocess_grd`). Each has a notebook driving its module.
- `features/polygon_to_swaths_bursts/` — Sentinel-1 SLC: which sub-swaths and
  bursts a polygon intersects, read from the product annotation XML without
  touching the image data. Module + CLI (`rosarium.py bursts`), a mirrored
  notebook, and a `README.md` detailing the algorithm, its accuracy limits and
  its usage. Used by `snap_gpt` to drive TOPSAR-Split (coarse mode there, the
  module itself defaults to strict). Not in the front-end yet.
- Not ported from the `geo` repo: `asf/` (gamma0 RTC through ASF HyP3). Left
  out on purpose. Still in `vigisar`, to be removed once the move is
  validated: `src/preprocess/`, `utils/parallel_download.py`,
  `vigisar_graphs/`.
