"""Parallel download of Sentinel-1 .SAFE products from CDSE via rclone.

Given a path file — the one `aoi_to_slc` writes, `data/utils/list.txt` by
default, one `/eodata/...` product path per line — this builds an rclone
filter file next to it, runs a single parallelised `rclone copy` covering
every product at once, then flattens the resulting date/mission tree so every
.SAFE folder ends up directly under `data/raw/<folder>/`. Products whose
.SAFE is already there are left out of the copy.

    python rosarium.py download                     # data/utils/list.txt -> data/raw/vrac/
    python rosarium.py download --list my.txt --folder zta1

rclone comes from conda-forge with the env, but its remote for the CDSE S3
endpoint is configured once by the user (`rclone config`, named `cdse` by
default — see the README).

`parallel_download` takes an optional ``reporter``, as the SNAP pipelines do
(see `features/snap_gpt/snap_gpt.py`): the webmap uses it to show rclone's
transfer statistics instead of the console progress.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
DEFAULT_LIST = ROOT / "data" / "utils" / "list.txt"

DEFAULT_REMOTE = "cdse:eodata"
DEFAULT_FOLDER = "vrac"
DEFAULT_TRANSFERS = 8
DEFAULT_MULTI_THREAD_STREAMS = 8


def _normalize_remote_path(raw: str) -> str:
    """Turn a raw S3-style product path into a path relative to the `eodata` bucket root.

    Accepts the three forms `aoi_to_slc` can write (`/eodata/...`,
    `s3://eodata/...`, `eodata/...`).
    """
    raw = raw.strip()
    if raw.lower().startswith("s3://"):
        raw = raw[5:]
    parts = [p for p in raw.split("/") if p and p != "**"]
    if parts and parts[0].lower() == "eodata":
        parts = parts[1:]
    return "/" + "/".join(parts)


def _raw_path_to_filter_line(raw: str) -> str:
    return f"+ {_normalize_remote_path(raw)}/**"


def product_name(raw: str):
    """The ``<product>.SAFE`` folder name a path-file line points to, or None.

    Works on the three path forms and on lines already in filter syntax
    (``+ /Sentinel-1/.../X.SAFE/**``).
    """
    for part in raw.strip().lstrip("+- ").split("/"):
        if part.upper().endswith(".SAFE"):
            return part
    return None


def missing_lines(raw_lines: list, dest_dir: Path) -> list:
    """The path-file lines whose product is not yet in ``dest_dir``.

    A .SAFE lands at the root of the folder only once the whole copy has
    succeeded (``flatten_safe_dirs`` runs after rclone), so its presence
    there means complete. Without this, a second run would copy it again:
    rclone looks for it at its bucket path (``Sentinel-1/SAR/...``), which
    the flattening emptied.
    """
    dest_dir = Path(dest_dir)
    return [
        raw for raw in raw_lines
        if raw.strip() and not (
            (name := product_name(raw)) and (dest_dir / name).is_dir()
        )
    ]


def build_filter_lines(raw_lines: list) -> list:
    """
    Convert path-file lines into rclone filter syntax.

    Lines already in filter syntax (starting with "+" or "-") are kept as-is,
    so the function is safe to re-run on a list that was already converted.
    A trailing "- *" catch-all (excluding everything else) is (re-)appended once.
    """
    lines = []
    seen = set()
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        if raw.startswith("+") or raw.startswith("-"):
            if raw.replace(" ", "") == "-*":
                continue
            line = raw
        else:
            line = _raw_path_to_filter_line(raw)
        if line not in seen:
            seen.add(line)
            lines.append(line)

    if not lines:
        raise ValueError("No product paths found to build the rclone filter from.")

    lines.append("- *")
    return lines


def build_filter_file(list_path: Path, raw_lines: list = None) -> Path:
    """Write the filter.txt of list_path next to it.

    ``raw_lines`` replaces the content of list_path when given (the lines
    left once the products already downloaded are dropped).
    """
    list_path = Path(list_path)
    if raw_lines is None:
        raw_lines = list_path.read_text(encoding="utf-8").splitlines()

    filter_lines = build_filter_lines(raw_lines)

    filter_path = list_path.parent / "filter.txt"
    filter_path.write_text("\n".join(filter_lines) + "\n", encoding="utf-8")

    return filter_path


def run_rclone_copy(
    filter_path: Path,
    dest_dir: Path,
    transfers: int = DEFAULT_TRANSFERS,
    multi_thread_streams: int = DEFAULT_MULTI_THREAD_STREAMS,
    remote: str = DEFAULT_REMOTE,
    reporter=None,
) -> None:
    """Run a single parallelized rclone copy of every product matched by filter_path into dest_dir.

    Without a reporter rclone draws its live progress in the console
    (``--progress``). With one, rclone logs JSON instead, one statistics
    record per second, which the reporter reads (``kind="rclone"``).
    """
    rclone = shutil.which("rclone")
    if rclone is None:
        raise FileNotFoundError(
            "rclone not found on the PATH. It ships with the rosarium env: activate "
            "the env, or update it from env_light_rosarium.yml if the package is "
            "missing. The CDSE remote itself is configured once with `rclone config` "
            "(see the README of features/download_products)."
        )
    cmd = [
        rclone, "copy", remote, ".",
        "--filter-from", str(filter_path),
        "--transfers", str(transfers),
        "--multi-thread-streams", str(multi_thread_streams),
    ]
    if reporter is None:
        subprocess.run(cmd + ["--progress"], cwd=str(dest_dir), check=True)
        return
    cmd += ["--use-json-log", "--stats", "1s", "--stats-log-level", "NOTICE"]
    code = reporter.run(cmd, cwd=str(dest_dir), kind="rclone")
    if code != 0:
        raise RuntimeError(f"rclone copy failed (exit code {code})")


def remove_empty_dirs(root: Path) -> None:
    """Remove every directory under root left empty after flattening (bottom-up, non-destructive)."""
    root = Path(root)
    for p in sorted(root.rglob("*"), key=lambda q: len(q.parts), reverse=True):
        if p.is_dir():
            try:
                p.rmdir()
            except OSError:
                pass  # not empty (or in use) — leave it alone


def flatten_safe_dirs(dest_dir: Path) -> list:
    """Move every *.SAFE directory found anywhere under dest_dir up to its root, then prune the empty tree."""
    dest_dir = Path(dest_dir)
    safe_dirs = sorted(p for p in dest_dir.rglob("*.SAFE") if p.is_dir())

    moved = []
    for safe_dir in safe_dirs:
        target = dest_dir / safe_dir.name
        if safe_dir == target:
            moved.append(target)
            continue
        if target.exists():
            print(f"[WARN] {target} already exists, skipping {safe_dir}")
            continue
        safe_dir.rename(target)
        moved.append(target)

    remove_empty_dirs(dest_dir)
    return moved


def parallel_download(
    list_path=None,
    folder: str = DEFAULT_FOLDER,
    transfers: int = DEFAULT_TRANSFERS,
    multi_thread_streams: int = DEFAULT_MULTI_THREAD_STREAMS,
    remote: str = DEFAULT_REMOTE,
    reporter=None,
) -> Path:
    """
    Download every Sentinel-1 product listed in list_path into data/raw/<folder>/.

    Products whose .SAFE is already at the root of that folder are skipped
    (see `missing_lines`); when none is left, rclone is not run at all.

    Args:
        list_path: Path to the list of remote product paths (default: data/utils/list.txt).
        folder: Sub-folder of data/raw to download into (created if missing, reused if present).
        transfers: rclone --transfers value.
        multi_thread_streams: rclone --multi-thread-streams value.
        remote: rclone remote:bucket to copy from.
        reporter: Optional progress receiver (see features/snap_gpt/snap_gpt.py):
            one step, ``download``, and rclone run through it.

    Returns:
        Path to data/raw/<folder>, containing the flattened .SAFE products.
    """
    say = reporter.log if reporter is not None else print
    if reporter is not None:
        reporter.step("download")

    list_path = Path(list_path) if list_path else DEFAULT_LIST
    if not list_path.is_file():
        raise FileNotFoundError(f"path file not found: {list_path}")
    dest_dir = RAW_DIR / folder
    dest_dir.mkdir(parents=True, exist_ok=True)

    raw_lines = [line for line in list_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    todo = missing_lines(raw_lines, dest_dir)
    if len(todo) < len(raw_lines):
        say(f"{len(raw_lines) - len(todo)} product(s) already in {dest_dir}, skipped")
    if not todo:
        say("Nothing left to download")
        return dest_dir

    filter_path = build_filter_file(list_path, todo)
    say(f"Filter file written to {filter_path}")

    run_rclone_copy(filter_path, dest_dir, transfers, multi_thread_streams, remote, reporter)

    moved = flatten_safe_dirs(dest_dir)
    say(f"{len(moved)} .SAFE product(s) available in {dest_dir}")

    return dest_dir


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

DEFAULT_PROG = "python download_products.py"


def _build_parser(prog=None):
    # `prog` is how the user invoked the tool — "python rosarium.py download"
    # from the repository entry point — so that usage and examples match
    prog = prog or DEFAULT_PROG
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Parallel download of Sentinel-1 .SAFE products from CDSE via rclone.\n\n"
            "Reads a path file (what the webmap / aoi_to_slc writes: one /eodata/...\n"
            "product path per line), builds an rclone filter file next to it, runs a\n"
            "single parallelized `rclone copy` for every product at once, then flattens\n"
            "the resulting mission/date tree so every .SAFE ends up directly under\n"
            "data/raw/<folder>/.  Products already there are skipped.\n\n"
            "rclone comes with the rosarium env; what it needs is a remote configured\n"
            "once for the CDSE S3 endpoint, with your own keys from\n"
            "https://eodata-s3keysmanager.dataspace.copernicus.eu/ (`rclone config`;\n"
            "the default remote name is `cdse`, bucket `eodata`)."
        ),
        epilog=(
            "examples:\n"
            f"  {prog}                                   # data/utils/list.txt -> data/raw/vrac/\n"
            f"  {prog} --list data/utils/paris.txt --folder paris\n"
            f"  {prog} --transfers 4 --multi-thread-streams 4   # gentler on the connection"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--list", default=str(DEFAULT_LIST), metavar="PATH",
                        help="[optional] Path file to download (default: data/utils/list.txt)")
    parser.add_argument("--folder", default=DEFAULT_FOLDER, metavar="NAME",
                        help=f"[optional] Sub-folder of data/raw to download into (default: {DEFAULT_FOLDER!r})")
    parser.add_argument("--transfers", type=int, default=DEFAULT_TRANSFERS, metavar="N",
                        help=f"[optional] rclone --transfers value (default: {DEFAULT_TRANSFERS})")
    parser.add_argument("--multi-thread-streams", type=int, default=DEFAULT_MULTI_THREAD_STREAMS, metavar="N",
                        help=f"[optional] rclone --multi-thread-streams value (default: {DEFAULT_MULTI_THREAD_STREAMS})")
    parser.add_argument("--remote", default=DEFAULT_REMOTE, metavar="REMOTE:BUCKET",
                        help=f"[optional] rclone remote to copy from (default: {DEFAULT_REMOTE!r})")
    return parser


def main(argv=None, prog=None):
    parser = _build_parser(prog)
    args = parser.parse_args(argv)
    parallel_download(
        list_path=args.list,
        folder=args.folder,
        transfers=args.transfers,
        multi_thread_streams=args.multi_thread_streams,
        remote=args.remote,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
