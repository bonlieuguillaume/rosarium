# download_products

Download the Sentinel-1 products of a path file from the Copernicus Data Space
(CDSE) with [rclone](https://rclone.org/), in parallel, into `data/raw/<folder>/`.
This is the step between [`aoi_to_slc`](../aoi_to_slc/README.md), which writes
the path file, and the pre/post pipelines, which read the `.SAFE` folders. No
notebook: it is a command, and the webmap calls it (the *Download* button of
the *Browse & download* tab, the first step of a *Pre / post* run).

- `download_products.py` — `parallel_download(list_path, folder, ...)`, the
  filter-file builder (`build_filter_lines`, `build_filter_file`), the rclone
  call (`run_rclone_copy`) and the flattening of the downloaded tree
  (`flatten_safe_dirs`). Command line: `python rosarium.py download`.

## How it works

1. The path file — `data/utils/list.txt` by default, one product per line in
   any of the three forms `aoi_to_slc` writes (`/eodata/...`, `s3://eodata/...`,
   `eodata/...`) — loses the products whose `.SAFE` is already at the root of
   `data/raw/<folder>/` (nothing is run when none is left), then the rest is
   turned into an rclone **filter file**, `filter.txt`, next to it: one
   `+ /Sentinel-1/SAR/.../<product>.SAFE/**` line per product and a final
   `- *` that excludes everything else. Lines already in filter syntax are
   kept, so a converted list can be fed again.
2. One `rclone copy cdse:eodata . --filter-from filter.txt` runs in
   `data/raw/<folder>/`, with `--transfers 8 --multi-thread-streams 8`: every
   product at once, several streams per file. rclone walks only the listed
   folders, so the bucket-wide filter costs nothing.
3. rclone reproduces the bucket's tree (`Sentinel-1/SAR/IW_SLC__1S/2026/09/11/…`);
   every `*.SAFE` folder is moved up to `data/raw/<folder>/` and the emptied
   tree pruned. A `.SAFE` already present is left alone (warning).

The download resumes: rclone skips files already complete, so an interrupted
run is simply launched again. And a product downloaded once is not downloaded
again: without step 1's check it would be, since rclone looks for it at its
bucket path, which the flattening emptied. A `.SAFE` reaches the root of the
folder only after the whole copy succeeded, so its presence there means
complete.

From the webmap, rclone logs JSON statistics once a second instead of drawing
its console progress (`reporter` argument of `parallel_download`, see
`features/snap_gpt/snap_gpt.py`): the page shows bytes received, speed, ETA
and files done.

## Configuring rclone

rclone is not a Python package but a single binary; it comes from conda-forge
with the rest of the env (`rclone` in `env_light_rosarium.yml`), so there is
nothing to download. It lands in `<env>/bin/` and is on the PATH as soon as
the env is activated — which is what the launchers of `launch/` do.

What does need doing once, and only once, is the **remote**: the binary knows
nothing of CDSE until it is given your S3 keys, which come from
<https://eodata-s3keysmanager.dataspace.copernicus.eu/>. Either run
`rclone config` (interactive) or paste this section into the file that
`rclone config file` points to:

```
[cdse]
type = s3
provider = Other
access_key_id = <your access key>
secret_access_key = <your secret key>
endpoint = https://eodata.dataspace.copernicus.eu
region = default
```

That config file is **per user, not per environment** (`%APPDATA%\rclone\` on
Windows, `~/.config/rclone/` elsewhere): it is written once and survives
reinstalling, moving or replacing the binary.

The remote name (`cdse`) and bucket (`eodata`) are the default of `--remote`;
another name is passed as `--remote name:eodata`. Check with
`rclone lsd cdse:eodata` — it should list `Sentinel-1`, `Sentinel-2`, …

## Usage

```
python rosarium.py download                                # data/utils/list.txt -> data/raw/vrac/
python rosarium.py download --list data/utils/paris.txt --folder paris
python rosarium.py download --transfers 4 --multi-thread-streams 4
python rosarium.py download --help
```

| Option | Default | Effect |
| --- | --- | --- |
| `--list PATH` | `data/utils/list.txt` | the path file to download |
| `--folder NAME` | `vrac` | sub-folder of `data/raw/` receiving the `.SAFE` |
| `--transfers N` | `8` | files copied in parallel |
| `--multi-thread-streams N` | `8` | streams per large file |
| `--remote REMOTE:BUCKET` | `cdse:eodata` | the rclone remote |

The module also runs on its own with the same options:
`python features/download_products/download_products.py ...`. From Python:

```python
from features.download_products.download_products import parallel_download

parallel_download("data/utils/list.txt", folder="zta1")   # -> Path to data/raw/zta1
```

## Dependencies

Standard library only on the Python side. rclone comes from conda-forge with
the env; its CDSE remote is configured once (above).
