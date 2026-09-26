"""Run the SNAP GPT graphs of `graphs/` on Sentinel-1 products.

One function per graph — `run_backscatter`, `run_coherence`, `run_gathering`
(SLC) and `run_backscatter_grd` (GRD) — plus `run_mosaic`, which merges the
per-sub-swath GeoTIFFs without SNAP. The pipelines of `features/pre_post/`
chain them; each is also reachable on its own:

    python rosarium.py gpt <backscatter|coherence|gathering|backscatter-grd|mosaic> ...

SNAP is the one external dependency: its `gpt` executable is looked for in
`SNAP_GPT`, on the PATH, then in the usual install folders (`find_gpt`), and
`--gpt` overrides that. Every graph runs in a sub-process; nothing else of
SNAP is used.

Every ``run_*`` function, and the pipelines built on them, take an optional
``reporter``. Without one (the command line), gpt writes straight to the
console, as it always did. With one (the webmap), the progress goes to it
instead; any object with these methods will do:

    reporter.plan(steps)           announce step names about to run, in order
                                   (added after those already announced)
    reporter.step(name)            step ``name`` starts (the previous one is done)
    reporter.info(key, value)      structured data for the page (JSON-able)
    reporter.log(text)             one line of log
    reporter.run(command, cwd=None, kind=None) -> int
                                   run a sub-process, read its output, return
                                   its exit code; ``kind`` ("gpt", "rclone")
                                   says how to read the progress. May raise to
                                   stop the run (cancellation).

`frontend/jobs.py` holds the one the webmap uses.
"""

import argparse
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # so the module also runs from its own folder
    sys.path.insert(0, str(ROOT))

from features.polygon_to_swaths_bursts.polygon_to_swaths_bursts import (  # noqa: E402
    get_intersecting_bursts,
    parse_polygon,
)


# ---------------------------------------------------------------------------
# Where things are
# ---------------------------------------------------------------------------

GRAPHS_DIR = Path(__file__).resolve().parent / "graphs"
# Outputs of the pre/post pipelines: data/preprocessed/pre_post/<name>/ (vrac
# when the pipeline is given no name), the intermediate .dim products in
# temp/, the single steps run without a name in default/
PRE_POST_DIR = ROOT / "data" / "preprocessed" / "pre_post"
TEMP_DIR = PRE_POST_DIR / "temp"
DEFAULT_DIR = PRE_POST_DIR / "default"
DEFAULT_RUN_NAME = "vrac"  # same as the default download folder, data/raw/vrac

GRAPH_BACKSCATTER = GRAPHS_DIR / "backscatter.xml"
GRAPH_COHERENCE = GRAPHS_DIR / "coherence.xml"
GRAPH_COHERENCE_ONE_BURST = GRAPHS_DIR / "coherence_one_burst.xml"
GRAPH_GATHERING = GRAPHS_DIR / "gathering.xml"
GRAPH_BACKSCATTER_GRD = GRAPHS_DIR / "backscatter_grd.xml"


def _gpt_candidates():
    """Usual locations of SNAP's gpt on this platform, most likely first."""
    home = Path.home()
    if sys.platform == "win32":
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        roots = [program_files / "esa-snap", program_files / "snap",
                 home / "esa-snap", home / "snap"]
        exe = "gpt.exe"
    elif sys.platform == "darwin":
        roots = [Path("/Applications/esa-snap"), Path("/Applications/snap"),
                 home / "esa-snap", home / "snap"]
        exe = "gpt"
    else:
        roots = [home / "esa-snap", home / "snap",
                 Path("/opt/esa-snap"), Path("/opt/snap"),
                 Path("/usr/local/esa-snap"), Path("/usr/local/snap")]
        exe = "gpt"
    return [root / "bin" / exe for root in roots]


def find_gpt() -> Optional[str]:
    """Locate SNAP's gpt executable, or None.

    Order: the ``SNAP_GPT`` environment variable, ``gpt`` on the PATH, then
    the usual install folders of the platform (``<Program Files>/esa-snap``,
    ``~/esa-snap``, ``/Applications/esa-snap``, ``/opt/esa-snap``...).
    """
    env = os.environ.get("SNAP_GPT")
    if env and Path(env).is_file():
        return str(Path(env))
    on_path = shutil.which("gpt")
    if on_path:
        return on_path
    for candidate in _gpt_candidates():
        if candidate.is_file():
            return str(candidate)
    return None


DEFAULT_GPT = find_gpt()


# ---------------------------------------------------------------------------
# GPT memory / performance settings
# ---------------------------------------------------------------------------
# Every graph run goes through _run_gpt, which passes these four settings on
# the gpt command line.  A command-line flag always overrides what SNAP has in
# gpt.vmoptions and ~/.snap/etc/snap.properties, so what is set here (or given
# to the CLIs) is what actually runs — the SNAP GUI settings do not apply.
# `gpt --diag` prints the values a bare gpt would use.
#
# The defaults below are the ones a 32 GB / 8-core (16-thread) machine runs
# comfortably.  How to choose them for another machine:
#
#   xmx        Java heap ceiling (-Xmx).  Everything gpt holds — the tile
#              cache AND the working arrays of the operators (coregistration,
#              Back-Geocoding, ESD keep whole bursts and the DEM in memory) —
#              must fit under it.  About 2/3 of the physical RAM, leaving the
#              rest to the OS, Python and the GeoTIFF tools.  Too low: a Java
#              OutOfMemoryError on large AOIs.  Too high: the machine swaps,
#              and the JVM can die on a native allocation (hs_err_pid*.log).
#
#   cache      Tile cache (-c), lives INSIDE the heap.  Keeps computed tiles so
#              that downstream operators do not recompute them.  A ceiling, not
#              a need: too small only costs time (recomputation), it never
#              crashes — whereas a big cache always fills up and starves the
#              operators.  1/4 to 1/3 of the heap is plenty for these graphs.
#
#   threads    Tiles computed in parallel (-q).  Working memory of the tiled
#              operators grows with it.  Up to the number of hardware threads;
#              the number of physical cores is the sweet spot when memory is
#              tight (SNAP scales poorly beyond ~8 threads anyway).  Lowering
#              it is the second lever after the cache on very large AOIs.
#
#   tile_size  Edge of the square tiles, in pixels.  Keep a power of two
#              (256, 512, 1024): it matches the block size of the files on disk
#              and of the pyramid levels, so every tile maps to whole blocks.
#              512 is SNAP's default and there is rarely a reason to change it;
#              1024 lowers the per-tile overhead on big rasters at the cost of
#              more memory per thread.
#
# Neither cache nor threads can shrink what the coregistration operators hold
# for a given AOI: if a large AOI does not fit, lower the cache first, then the
# threads, and if it still fails the heap (hence the machine) is the limit.

DEFAULT_XMX = "21G"
DEFAULT_CACHE = "8192M"
DEFAULT_THREADS = 16
DEFAULT_TILE_SIZE = 512


@dataclass
class GptOptions:
    """Memory / performance settings passed to every gpt call (see above).

    ``xmx`` and ``cache`` are Java size strings (``"21G"``, ``"8192M"``).
    """
    xmx: str = DEFAULT_XMX
    cache: str = DEFAULT_CACHE
    threads: int = DEFAULT_THREADS
    tile_size: int = DEFAULT_TILE_SIZE

    def to_args(self) -> list[str]:
        """The gpt command-line flags for these settings.

        ``-J<opt>`` hands the option to the JVM itself (heap, and system
        properties, which is how snap.properties keys are overridden);
        ``-c`` / ``-q`` are gpt's own flags.
        """
        return [
            f"-J-Xmx{self.xmx}",
            f"-J-Dsnap.jai.defaultTileSize={self.tile_size}",
            "-c", self.cache,
            "-q", str(self.threads),
        ]


DEFAULT_GPT_OPTIONS = GptOptions()


def add_gpt_options(parser: argparse.ArgumentParser) -> None:
    """Add --gpt and --xmx / --cache / --threads / --tile-size to a CLI parser."""
    parser.add_argument(
        "--gpt", default=DEFAULT_GPT, metavar="PATH",
        help="SNAP's gpt executable (default: SNAP_GPT env var, then the PATH, then "
        f"the usual install folders; currently {DEFAULT_GPT or 'not found'})",
    )
    g = parser.add_argument_group(
        "GPT memory / performance",
        "Override SNAP's settings for this run (a flag always wins over "
        "gpt.vmoptions and snap.properties).  Rules of thumb: xmx ~ 2/3 of the "
        "RAM; cache 1/4-1/3 of xmx (too small only costs time, too big starves "
        "the operators); threads <= hardware threads, physical cores when memory "
        "is tight; tile-size a power of two, 512 unless you know why.",
    )
    g.add_argument("--xmx", default=DEFAULT_XMX, metavar="SIZE",
                   help=f"Java heap ceiling, e.g. 16G (default: {DEFAULT_XMX})")
    g.add_argument("--cache", default=DEFAULT_CACHE, metavar="SIZE",
                   help=f"tile cache, inside the heap, e.g. 4096M (default: {DEFAULT_CACHE})")
    g.add_argument("--threads", default=DEFAULT_THREADS, type=int, metavar="N",
                   help=f"tiles computed in parallel (default: {DEFAULT_THREADS})")
    g.add_argument("--tile-size", default=DEFAULT_TILE_SIZE, type=int, metavar="PX",
                   help=f"tile edge in pixels, power of two (default: {DEFAULT_TILE_SIZE})")


def gpt_options_from_args(args: argparse.Namespace) -> GptOptions:
    """Build a GptOptions from a namespace produced with add_gpt_options."""
    return GptOptions(
        xmx=args.xmx, cache=args.cache, threads=args.threads, tile_size=args.tile_size
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clean_band_name(collocate_name: str) -> str:
    """Derive a short generic name from a Collocate output band name.

    Examples:
        Gamma0_IW2_VH_mst_17Aug2017_M  ->  gamma0_VH
        coh_IW2_VV_05Aug2017_17Aug2017_S0  ->  coh_VV
    """
    pol = "VH" if "_VH_" in collocate_name else ("VV" if "_VV_" in collocate_name else "")
    if collocate_name.startswith("Gamma0"):
        return f"gamma0_{pol}" if pol else "gamma0"
    if collocate_name.startswith("coh"):
        return f"coh_{pol}" if pol else "coh"
    return collocate_name


def _clean_geotiff(path: Path, band_names: list[str]) -> None:
    """Remove extra flag bands added by SNAP's Collocate (collocationFlags),
    rename the remaining bands, and declare 0.0 as the NoData value.

    SNAP's Write operator appends flag bands after data bands regardless of the
    BandSelect node.  We keep only the first len(band_names) bands and discard
    the rest, then set the band descriptions in-place.

    SNAP fills masked pixels (sea via nodataValueAtSea, out-of-swath areas)
    with the band no-data value 0.0, but that declaration is lost when the
    BEAM-DIMAP bands are converted to GeoTIFF, so it is re-set here on every
    band (a linear Gamma0 or coherence of exactly 0.0 does not occur in
    practice, so this is safe).
    """
    try:
        from osgeo import gdal
    except ImportError:
        print("Warning: osgeo.gdal not available - band cleanup skipped.", file=sys.stderr)
        return

    path = Path(path)
    gdal.UseExceptions()
    gdal.PushErrorHandler("CPLQuietErrorHandler")
    ds = gdal.Open(str(path))
    gdal.PopErrorHandler()
    if ds is None:
        print(f"Warning: could not open {path}.", file=sys.stderr)
        return

    n_expected = len(band_names)
    n_actual = ds.RasterCount
    ds = None

    if n_actual > n_expected:
        tmp = path.with_name(path.name + ".tmp.tif")
        gdal.PushErrorHandler("CPLQuietErrorHandler")
        gdal.Translate(str(tmp), str(path), bandList=list(range(1, n_expected + 1)))
        gdal.PopErrorHandler()
        os.replace(tmp, path)

    gdal.PushErrorHandler("CPLQuietErrorHandler")
    ds = gdal.Open(str(path), gdal.GA_Update)
    gdal.PopErrorHandler()
    if ds is None:
        return
    for i, name in enumerate(band_names, 1):
        band = ds.GetRasterBand(i)
        band.SetDescription(name)
        band.SetNoDataValue(0.0)
    ds = None


def _add_swath_suffix(path: Path, swath: str) -> Path:
    """Insert _IW1 / _IW2 / _IW3 before the file extension."""
    path = Path(path)
    return path.with_name(f"{path.stem}_{swath}{path.suffix}")


def _is_path(name: str) -> bool:
    """A run label ("zta1") or a path ("data/preprocessed/pre_post/zta6/zta6_slc")?"""
    return "/" in name or os.sep in name


def _aoi_to_wkt(aoi: str) -> str:
    """Normalise the AOI to an inline WKT string, the only form GPT accepts.

    ``aoi`` may be an inline WKT string or a path to a WKT / GeoJSON file
    (see ``polygon_to_swaths_bursts.parse_polygon``); the graphs' Subset node
    reads ``${aoi}`` as WKT, so a file is parsed and re-serialised here.
    """
    return parse_polygon(aoi).wkt


def polygon_to_swaths_bursts(product_path: str, aoi: str, coarse: bool = True) -> list[dict]:
    """
    Find which Sentinel-1 IW subswath(es) and burst range intersect the AOI.

    Thin wrapper around ``get_intersecting_bursts`` of the
    ``polygon_to_swaths_bursts`` feature that reshapes its
    ``{swath: [burst numbers]}`` summary into the triplets TOPSAR-Split
    expects (``subswath``, ``first_burst``, ``last_burst``).  See that
    feature's README for how the footprints are rebuilt from the annotation
    XML.

    Why ``coarse`` defaults to True here (the feature itself defaults to False):
    the footprints are rebuilt from the geolocation grid, whose rows sit on the
    burst boundaries, so consecutive bursts *touch* without overlapping and
    the outline is only accurate to ~1 km near the edges — whereas the real
    valid data of neighbouring bursts overlap by about a kilometre.  An AOI
    whose edge falls in that band can therefore need a burst the strict test
    misses.  In these pipelines the cost of the two errors is very asymmetric:
    the AOI is also the Subset clip applied after terrain correction, so a
    missing burst does not raise anything — it leaves a nodata hole inside the
    final GeoTIFF, which surfaces much later as spurious "changes" in a
    detection.  An extra burst only costs a few seconds of processing and is
    stitched cleanly by TOPSAR-Deburst.  Dilating the footprints by ~2 km
    before the test (``coarse=True``) buys the recall at that price.

    Args:
        product_path (str): Sentinel-1 SLC product (.zip archive or .SAFE directory).
        aoi (str): Area of interest in lon/lat WGS84 — inline WKT, or a path
            to a WKT / GeoJSON file.
        coarse (bool): Dilate the footprints before the test (default True,
            see above).  Set False to reproduce the feature's strict result.

    Returns:
        List of dicts, one per intersecting subswath, sorted by subswath, e.g.::

            [{"subswath": "IW2", "first_burst": 3, "last_burst": 5}]

        Empty if no subswath intersects the AOI.
    """
    _, summary = get_intersecting_bursts(product_path, aoi, coarse=coarse)
    return [
        {"subswath": swath, "first_burst": min(bursts), "last_burst": max(bursts)}
        for swath, bursts in sorted(summary.items())
    ]


def _read_dimap_band_names(dim_path: Path) -> list[str]:
    root = ET.parse(str(dim_path)).getroot()
    return [el.text for el in root.findall(".//Spectral_Band_Info/BAND_NAME")]


def _resolve_gathering_bands(
    input_backscatter: Path,
    input_coh_pre: Path,
    input_coh_post: Path,
) -> tuple[list[str], list[str]]:
    """
    Derive which Collocate output bands belong to the pre-event and post-event
    products by reading band names from the three BEAM-DIMAP inputs.

    Band names produced by SNAP (dates, ``_mst``/``_slv``, subswath) are not
    reliable, so nothing is parsed from them.  Instead the names Collocate will
    produce are *predicted* from the input band names + a fixed suffix, and
    the pre/post split is done purely by band position.

    Collocate suffix convention (must match the gathering.xml sources order
    AND ``referenceProductName`` = backscatter, see the header comment in
    ``graphs/gathering.xml``):
        input_backscatter -> reference -> ``_M``
        input_coh_pre     -> first secondary -> ``_S0``
        input_coh_post    -> second secondary -> ``_S1``

    SNAP's CreateStack always writes master bands before slave bands.
    Since run_backscatter is called with input1=pre2 (master) and input2=post1
    (slave), the first half of backscatter bands is always pre2 and the second
    half is always post1 — no date parsing required.

    Hidden assumptions (breaking any of them will NOT raise an error, the
    products will just be silently mislabelled):
        * The three ``<sourceProduct>`` of the Collocate node in gathering.xml
          keep the order backscatter / coh_pre / coh_post.  Swapping the two
          coherence inputs swaps pre and post coherence (same band count).
        * ``run_backscatter`` keeps input1=pre2, input2=post1, and the first
          ``<sourceProduct>`` of CreateStack in backscatter.xml is input1.
        * The backscatter stack contains exactly master + slave bands; the
          even-count check below cannot detect two extra bands.

    Returns:
        (bands_pre, bands_post): lists of band names as they appear after
        Collocate, ready to be passed as comma-separated sourceBands parameters.

    Raises:
        ValueError: If the backscatter product does not contain an even number
            of bands (would indicate something other than one master + one slave).
    """
    coh_pre_bands = _read_dimap_band_names(input_coh_pre)
    coh_post_bands = _read_dimap_band_names(input_coh_post)
    bs_bands = _read_dimap_band_names(input_backscatter)

    n = len(bs_bands)
    if n % 2 != 0:
        raise ValueError(
            f"Expected an even number of bands in the backscatter product "
            f"(one master image + one slave image), got {n} in {input_backscatter}"
        )
    bs_pre = bs_bands[:n // 2]   # master (pre2) — always listed first by SNAP
    bs_post = bs_bands[n // 2:]  # slave  (post1) — always listed second

    bands_pre = [f"{b}_M" for b in bs_pre] + [f"{b}_S0" for b in coh_pre_bands]
    bands_post = [f"{b}_M" for b in bs_post] + [f"{b}_S1" for b in coh_post_bands]

    return bands_pre, bands_post


def step_name(graph: str, swath: Optional[str] = None) -> str:
    """Name of one graph run in a reporter's step list: ``"coherence pre IW2"``.

    The pipelines announce the list up front (``reporter.plan``) and the
    ``run_*`` functions mark each step as it starts: both build the names
    here so that they match.
    """
    return f"{graph} {swath}" if swath else graph


def _swath_of(path) -> Optional[str]:
    """The ``IWx`` suffix of a per-swath intermediate (``backscatter_IW2.dim``), or None."""
    tail = Path(path).stem.rsplit("_", 1)[-1]
    return tail if tail in ("IW1", "IW2", "IW3") else None


def _run_gpt(
    gpt_path: Optional[str],
    graph_xml: Path,
    params: dict,
    gpt_options: GptOptions = DEFAULT_GPT_OPTIONS,
    reporter=None,
    step: Optional[str] = None,
) -> bool:
    """
    Internal helper: build a GPT command from a parameter dict and execute it.

    Parameters are passed as ``-Pkey=value`` flags, substituting ``${key}``
    placeholders in the XML graph.  Memory / performance flags come from
    ``gpt_options`` (see the GptOptions section at the top of this module).
    With a ``reporter`` (see the module docstring), ``step`` is marked as
    started and the reporter runs gpt; without one, gpt writes to the console.
    Returns True on success, False on failure.

    Raises:
        FileNotFoundError: If gpt_path is None or does not exist.
    """
    if not gpt_path or not Path(gpt_path).is_file():
        raise FileNotFoundError(
            f"SNAP's gpt executable not found ({gpt_path!r}). Install SNAP, then "
            "either put its bin/ folder on the PATH, set the SNAP_GPT environment "
            "variable, or pass --gpt PATH."
        )

    # -e -> full Java stack trace on error; the rest: heap, tile size, cache,
    # threads — every one of them overrides SNAP's own configuration files.
    command = [str(gpt_path), str(graph_xml), "-e", *gpt_options.to_args()]
    for key, value in params.items():
        command.append(f"-P{key}={value}")

    if reporter is not None:
        if step:
            reporter.step(step)
        reporter.log(f"Running graph: {Path(graph_xml).name}")
        return reporter.run(command, kind="gpt") == 0

    print(f"Running graph: {Path(graph_xml).name}")
    try:
        subprocess.run(command, check=True, text=True)
        print("Processing completed successfully.")
        return True
    except subprocess.CalledProcessError:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_backscatter(
    input1: str,
    input2: str,
    aoi: str,
    output: Optional[str] = None,
    gpt_path: Optional[str] = DEFAULT_GPT,
    gpt_options: GptOptions = DEFAULT_GPT_OPTIONS,
    reporter=None,
) -> list[bool]:
    """
    Run the backscatter graph on two Sentinel-1 SLC products.

    Processing chain:
        Apply-Orbit-File -> TOPSAR-Split -> ThermalNoiseRemoval -> Calibration
        -> TOPSAR-Deburst -> CreateStack -> Cross-Correlation -> Warp
        -> Speckle-Filter -> Terrain-Correction -> Subset -> Write

    The subswath and burst range are determined automatically from ``aoi`` using
    ``polygon_to_swaths_bursts`` (coarse mode, see its docstring).  If the AOI
    spans multiple subswaths the graph is run once per subswath and the outputs
    are suffixed with the subswath name (e.g. ``backscatter_IW2.dim``).

    Args:
        input1 (str): Path to the master Sentinel-1 SLC product (.zip or .SAFE).
            Should be the pre2 image so that the master bands appear first in the
            output stack (required by ``run_gathering``).
        input2 (str): Path to the secondary Sentinel-1 SLC product (.zip or .SAFE).
            Should be the post1 image.
        aoi (str): Area of interest in lon/lat WGS84 — inline WKT, or a path to
            a WKT / GeoJSON file.  Used both to locate the correct subswath/burst
            range and to spatially clip the Terrain-Correction output via the
            Subset node.
        output (str, optional): Full path for the output product (.dim).
            Defaults to ``data/preprocessed/pre_post/temp/backscatter.dim``.
            If multiple subswaths are found, the subswath name is inserted before
            the extension (e.g. ``backscatter_IW2.dim``).
        gpt_path (str): Path to the SNAP GPT executable (default: ``find_gpt()``).
        gpt_options (GptOptions): Heap / cache / threads / tile size for gpt
            (see the top of this module).
        reporter (optional): Progress receiver, see the module docstring;
            one step per subswath, ``backscatter IWx``.

    Returns:
        list[bool]: One entry per processed subswath, True when gpt succeeded.

    Raises:
        FileNotFoundError: If gpt_path does not exist.
        ValueError: If the AOI does not intersect any subswath in input1.
    """
    swaths = polygon_to_swaths_bursts(input1, aoi)
    if not swaths:
        raise ValueError(f"The AOI does not intersect any subswath in {input1}")
    aoi_wkt = _aoi_to_wkt(aoi)

    base_path = Path(output) if output is not None else TEMP_DIR / "backscatter.dim"
    base_path.resolve().parent.mkdir(parents=True, exist_ok=True)

    results = []
    for swath in swaths:
        out = _add_swath_suffix(base_path, swath["subswath"]) if len(swaths) > 1 else base_path
        params = {
            "input1": input1,
            "input2": input2,
            "output": str(out),
            "aoi": aoi_wkt,
            "subswath": swath["subswath"],
            "first_burst": str(swath["first_burst"]),
            "last_burst": str(swath["last_burst"]),
        }
        results.append(_run_gpt(gpt_path, GRAPH_BACKSCATTER, params, gpt_options,
                                reporter, step_name("backscatter", swath["subswath"])))

    return results


def run_coherence(
    input1: str,
    input2: str,
    aoi: str,
    pair: Optional[str] = None,
    output: Optional[str] = None,
    gpt_path: Optional[str] = DEFAULT_GPT,
    gpt_options: GptOptions = DEFAULT_GPT_OPTIONS,
    reporter=None,
) -> list[bool]:
    """
    Run the coherence graph on two Sentinel-1 SLC products.

    Processing chain:
        Apply-Orbit-File -> TOPSAR-Split -> Back-Geocoding
        -> Enhanced-Spectral-Diversity -> Coherence -> TOPSAR-Deburst
        -> Terrain-Correction -> Subset -> Write

    The subswath and burst range are determined automatically from ``aoi``.
    If the AOI spans multiple subswaths the graph is run once per subswath
    and the subswath name is inserted before the file extension
    (e.g. ``coherence_pre_IW1.dim``, ``coherence_pre_IW2.dim``).

    When a subswath keeps a single burst, ``coherence_one_burst.xml`` is used
    instead of ``coherence.xml``: the two graphs are identical except that the
    former has no Enhanced-Spectral-Diversity node.  ESD refines the azimuth
    coregistration from the overlap between consecutive bursts, so with one
    burst it has nothing to estimate and the graph yields an empty product.

    Two output modes depending on ``pair``:

    * **No pair** (standalone run): output is written to
      ``data/preprocessed/pre_post/default/coherence.dim``, or to ``output``
      if given as a full path.  Useful for single-date coherence products or
      exploratory runs.

    * **With pair** (``"pre"`` or ``"post"``): output is written to
      ``data/preprocessed/pre_post/temp/coherence_{pair}.dim``.  Use this mode
      when running the full pipeline (backscatter + coherence pre + coherence
      post + gathering) so that ``run_gathering`` can locate the files
      automatically.

    Args:
        input1 (str): Path to the master Sentinel-1 SLC product (.zip or .SAFE).
        input2 (str): Path to the secondary Sentinel-1 SLC product (.zip or .SAFE).
        aoi (str): Area of interest in lon/lat WGS84 — inline WKT, or a path to
            a WKT / GeoJSON file.
        pair (str, optional): ``"pre"`` or ``"post"``.  When given, the output is
            placed in the temp folder with a ``_pre`` / ``_post`` suffix so that
            ``run_gathering`` can find it.  When omitted, the output goes to
            ``data/preprocessed/pre_post/default/coherence.dim`` (or the path
            given in ``output``).
        output (str, optional):
            * If ``pair`` is given: base name used in the temp folder filename,
              e.g. ``"zta1"`` -> ``.../temp/zta1_pre.dim``.  Defaults to
              ``"coherence"``.
            * If ``pair`` is not given: full path to the output file.
              Defaults to ``data/preprocessed/pre_post/default/coherence.dim``.
        gpt_path (str): Path to the SNAP GPT executable (default: ``find_gpt()``).
        gpt_options (GptOptions): Heap / cache / threads / tile size for gpt
            (see the top of this module).
        reporter (optional): Progress receiver, see the module docstring;
            one step per subswath, ``coherence <pair> IWx``.

    Returns:
        list[bool]: One entry per processed subswath, True when gpt succeeded.

    Raises:
        FileNotFoundError: If gpt_path does not exist.
        ValueError: If pair is not ``"pre"``, ``"post"``, or None.
        ValueError: If the AOI does not intersect any subswath in input1.
    """
    if pair is not None and pair not in ("pre", "post"):
        raise ValueError(f"pair must be 'pre', 'post', or None, got {pair!r}")

    swaths = polygon_to_swaths_bursts(input1, aoi)
    if not swaths:
        raise ValueError(f"The AOI does not intersect any subswath in {input1}")
    aoi_wkt = _aoi_to_wkt(aoi)

    if pair is None:
        base_path = Path(output) if output is not None else DEFAULT_DIR / "coherence.dim"
        folder = base_path.resolve().parent
    else:
        base_name = output if output is not None else "coherence"
        folder = TEMP_DIR
        base_path = folder / f"{base_name}_{pair}.dim"

    folder.mkdir(parents=True, exist_ok=True)

    results = []
    for swath in swaths:
        out = _add_swath_suffix(base_path, swath["subswath"]) if len(swaths) > 1 else base_path
        params = {
            "input1": input1,
            "input2": input2,
            "output": str(out),
            "aoi": aoi_wkt,
            "subswath": swath["subswath"],
            "first_burst": str(swath["first_burst"]),
            "last_burst": str(swath["last_burst"]),
        }
        # ESD needs at least two bursts (see docstring): one burst -> the
        # variant without it
        one_burst = swath["first_burst"] == swath["last_burst"]
        graph = GRAPH_COHERENCE_ONE_BURST if one_burst else GRAPH_COHERENCE
        label = f"coherence {pair}" if pair else "coherence"
        results.append(_run_gpt(gpt_path, graph, params, gpt_options,
                                reporter, step_name(label, swath["subswath"])))

    return results


def _pre_post_outputs(output: Optional[str], ext: str = "") -> tuple[Path, Path, Path]:
    """Where the pre / post products of a run go, from its ``output`` argument.

    * ``None``          -> ``data/preprocessed/pre_post/default/pre`` and ``post``
    * Simple name       -> ``data/preprocessed/pre_post/<name>/<name>_pre`` and ``_post``
    * Full path prefix  -> ``<prefix>_pre`` and ``<prefix>_post`` (the caller
      controls the directory; the pipelines use it to keep the per-swath files
      of one run in the same folder)

    Returns (folder, pre, post), with ``ext`` appended to the two paths.
    """
    if output is None:
        folder = DEFAULT_DIR
        pre, post = folder / f"pre{ext}", folder / f"post{ext}"
    elif _is_path(output):
        prefix = Path(output).resolve()
        folder = prefix.parent
        pre, post = folder / f"{prefix.name}_pre{ext}", folder / f"{prefix.name}_post{ext}"
    else:
        folder = PRE_POST_DIR / output
        pre, post = folder / f"{output}_pre{ext}", folder / f"{output}_post{ext}"
    return folder, pre, post


def run_gathering(
    input_backscatter: str,
    input_coh_pre: str,
    input_coh_post: str,
    output: Optional[str] = None,
    gpt_path: Optional[str] = DEFAULT_GPT,
    gpt_options: GptOptions = DEFAULT_GPT_OPTIONS,
    reporter=None,
) -> list[str]:
    """
    Run the gathering graph to collocate a backscatter product with two
    coherence stacks and split the result into pre-event and post-event products.

    Processing chain:
        3x Read -> Collocate -> BandSelect -> Write (pre)
                             -> BandSelect -> Write (post)

    Band assignment is derived automatically from the BEAM-DIMAP band names
    of the three inputs — no XML editing required.

    Collocate lays everything on the grid of the backscatter product, the
    reference.  The three inputs come from separate graph runs, each of
    which chose its own UTM zone (``AUTO:42001``): when a coherence product
    landed in another zone than the backscatter, Collocate resamples it
    onto the backscatter grid (nearest neighbour), with no error or
    warning — one extra reprojection of the coherence bands, gamma0 never
    touched.  See *UTM zone* in the README.

    Args:
        input_backscatter (str): Path to the backscatter stack (.dim), output of
            ``run_backscatter``.  input1=pre2 and input2=post1 must have been
            respected when running the backscatter graph so that master bands
            (pre2) appear first in the stack.
        input_coh_pre (str): Path to the pre-event coherence product (.dim),
            output of ``run_coherence`` with ``pair="pre"``.
        input_coh_post (str): Path to the post-event coherence product (.dim),
            output of ``run_coherence`` with ``pair="post"``.
        output (str, optional): Name for this processing run.  A subfolder with
            that name is created under ``data/preprocessed/pre_post/`` and the
            two output GeoTIFFs are written there as ``<name>_pre.tif`` and
            ``<name>_post.tif``.  A path prefix (``.../zta6/zta6_slc``) writes
            ``zta6_slc_pre.tif`` / ``zta6_slc_post.tif`` in that folder.  If
            None, outputs go to ``data/preprocessed/pre_post/default/`` as
            ``pre.tif`` and ``post.tif``.
        gpt_path (str): Path to the SNAP GPT executable (default: ``find_gpt()``).
        gpt_options (GptOptions): Heap / cache / threads / tile size for gpt
            (see the top of this module).
        reporter (optional): Progress receiver, see the module docstring;
            one step, ``gathering IWx`` (``gathering`` when the backscatter
            input carries no subswath suffix).

    Returns:
        list[str]: Paths of the GeoTIFF files that were successfully written
            (``[pre.tif, post.tif]``).  Empty if GPT failed.

    Raises:
        FileNotFoundError: If gpt_path does not exist.
    """
    folder, output_pre, output_post = _pre_post_outputs(output)
    folder.mkdir(parents=True, exist_ok=True)

    input_backscatter = Path(input_backscatter)
    reference_name = input_backscatter.stem
    bands_pre, bands_post = _resolve_gathering_bands(
        input_backscatter, Path(input_coh_pre), Path(input_coh_post)
    )
    params = {
        "input1": str(input_backscatter),
        "input2": str(input_coh_pre),
        "input3": str(input_coh_post),
        "output_pre": str(output_pre),
        "output_post": str(output_post),
        "reference_name": reference_name,
        "bands_pre": ",".join(bands_pre),
        "bands_post": ",".join(bands_post),
    }
    _run_gpt(gpt_path, GRAPH_GATHERING, params, gpt_options,
             reporter, step_name("gathering", _swath_of(input_backscatter)))

    produced = []
    clean_pre = [_clean_band_name(b) for b in bands_pre]
    clean_post = [_clean_band_name(b) for b in bands_post]
    for tif, names in [
        (output_pre.with_name(output_pre.name + ".tif"), clean_pre),
        (output_post.with_name(output_post.name + ".tif"), clean_post),
    ]:
        if tif.exists():
            _clean_geotiff(tif, names)
            produced.append(str(tif))

    return produced


def _split_grd_stack(dim_path: Path, pre_path: Path, post_path: Path) -> list[str]:
    """
    Split a coregistered GRD stack (BEAM-DIMAP) into two GeoTIFFs.

    SNAP's CreateStack writes master bands before slave bands.  Since
    run_backscatter_grd is always called with input1=pre (master) and
    input2=post (slave), the first half of bands is pre and the second half
    is post — no date parsing required.

    Band names are read from the DIMAP XML.  If the "_mst" / "_slv" suffixes
    are present they are used to identify each group; otherwise the bands are
    split by position (first half / second half).
    """
    try:
        from osgeo import gdal
    except ImportError:
        print("Warning: osgeo.gdal not available - cannot split GRD stack.", file=sys.stderr)
        return []

    gdal.UseExceptions()

    dim_path = Path(dim_path)
    band_names = _read_dimap_band_names(dim_path)
    n = len(band_names)

    mst_idx = [i + 1 for i, name in enumerate(band_names) if "_mst" in name.lower()]
    slv_idx = [i + 1 for i, name in enumerate(band_names) if "_slv" in name.lower()]

    if not mst_idx or not slv_idx:
        if n % 2 != 0:
            raise ValueError(
                f"Cannot split GRD stack: odd number of bands ({n}) in {dim_path}"
            )
        half = n // 2
        mst_idx = list(range(1, half + 1))
        slv_idx = list(range(half + 1, n + 1))

    mst_names = [_clean_band_name(band_names[i - 1]) for i in mst_idx]
    slv_names = [_clean_band_name(band_names[i - 1]) for i in slv_idx]

    data_dir = dim_path.with_suffix(".data")
    produced = []
    for path, indices, names in [
        (Path(pre_path), mst_idx, mst_names),
        (Path(post_path), slv_idx, slv_names),
    ]:
        # BEAM-DIMAP stores each band as <name>.img + <name>.hdr (ENVI) inside
        # the .data/ directory. GDAL cannot open the .dim XML directly, so we
        # open individual .img files and merge them into one GeoTIFF via a VRT.
        raw_names = [band_names[i - 1] for i in indices]
        img_paths = [str(data_dir / f"{bn}.img") for bn in raw_names]

        vrt = gdal.BuildVRT("", img_paths, separate=True)
        if vrt is None:
            continue
        gdal.Translate(str(path), vrt, format="GTiff")
        vrt = None

        if path.exists():
            _clean_geotiff(path, names)
            produced.append(str(path))

    return produced


def run_backscatter_grd(
    pre: str,
    post: str,
    aoi: str,
    output: Optional[str] = None,
    gpt_path: Optional[str] = DEFAULT_GPT,
    gpt_options: GptOptions = DEFAULT_GPT_OPTIONS,
    reporter=None,
) -> list[str]:
    """
    Run the GRD backscatter graph on two Sentinel-1 GRD products and write
    two separate GeoTIFFs (pre-event and post-event).

    Processing chain (single graph, both images together):
        Apply-Orbit-File -> ThermalNoiseRemoval -> Remove-GRD-Border-Noise
        -> Calibration (x2) -> CreateStack -> Cross-Correlation -> Warp
        -> Speckle-Filter -> Terrain-Correction -> Subset -> Write

    The two images are coregistered via Cross-Correlation + Warp so that they
    lie on the same pixel grid.  The output BEAM-DIMAP stack is then split
    into two GeoTIFFs: the master bands (pre) and the slave bands (post).

    Unlike the SLC pipeline there is no subswath/burst splitting — GRD products
    already cover the full swath and do not require TOPSAR-Split.

    Args:
        pre (str): Path to the pre-event Sentinel-1 GRD product (.zip or .SAFE).
            Used as the master image (reference for coregistration).
        post (str): Path to the post-event Sentinel-1 GRD product (.zip or .SAFE).
            Used as the slave image.
        aoi (str): Area of interest in lon/lat WGS84 — inline WKT, or a path to
            a WKT / GeoJSON file.  Used to clip the output after terrain
            correction.
        output (str, optional): Controls where the two output GeoTIFFs are written.
            Follows the same convention as ``run_gathering``:

            * ``None``          -> ``data/preprocessed/pre_post/default/pre.tif`` and ``post.tif``
            * Simple name       -> ``data/preprocessed/pre_post/<name>/<name>_pre.tif`` and ``_post.tif``
            * Full path prefix  -> ``<prefix>_pre.tif`` and ``<prefix>_post.tif``
              (the folder is created if needed)
        gpt_path (str): Path to the SNAP GPT executable (default: ``find_gpt()``).
        gpt_options (GptOptions): Heap / cache / threads / tile size for gpt
            (see the top of this module).
        reporter (optional): Progress receiver, see the module docstring;
            two steps, ``backscatter_grd`` then ``split pre/post``.

    Returns:
        list[str]: Paths of the two GeoTIFFs that were successfully written
            (``[pre.tif, post.tif]``).  Empty if GPT failed.

    Raises:
        FileNotFoundError: If gpt_path does not exist.
    """
    folder, output_pre, output_post = _pre_post_outputs(output, ext=".tif")
    folder.mkdir(parents=True, exist_ok=True)

    tmp_dim = TEMP_DIR / "backscatter_grd.dim"
    tmp_data = TEMP_DIR / "backscatter_grd.data"
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # Remove any partial output from a previous failed run so SNAP starts clean.
    for path in (tmp_dim, tmp_data):
        if path.exists():
            try:
                shutil.rmtree(path) if path.is_dir() else path.unlink()
            except PermissionError:
                print(f"Warning: cannot delete {path} (file locked by another process). "
                      "Close SNAP GUI if it has this file open.", file=sys.stderr)

    success = _run_gpt(gpt_path, GRAPH_BACKSCATTER_GRD, {
        "input1": pre,
        "input2": post,
        "aoi": _aoi_to_wkt(aoi),
        "output": str(tmp_dim),
    }, gpt_options, reporter, step_name("backscatter_grd"))

    if not success or not tmp_dim.exists():
        return []

    if reporter is not None:
        reporter.step(step_name("split pre/post"))
    return _split_grd_stack(tmp_dim, output_pre, output_post)


def run_mosaic(inputs: list[str], output: str) -> str:
    """
    Merge a list of co-registered GeoTIFFs (one per subswath) into a single file.

    Intended for use after ``run_gathering`` when the AOI spans multiple subswaths:
    pass the per-swath pre (or post) GeoTIFFs and receive a single mosaicked file.

    Where inputs overlap, each pixel takes the value of the input in which it
    lies **farthest from an invalid pixel**.  Adjacent subswaths overlap by
    1-2 km, and every per-swath product is degraded along its own swath edge:
    SNAP leaves a 1-px line of NaN in the gamma0 bands there, and the
    coherence bands are zeroed over a wider fringe than the backscatter ones.
    A plain "last input wins" Warp with ``srcNodata=0`` copies the NaN line
    over the neighbour's good data (NaN is not 0) — one black line per swath
    edge in the mosaic.  Ranking by distance to the nearest invalid pixel
    hands those fringes to the other swath's interior, while on the outer
    boundary of the AOI, where only one input exists, nothing is lost.  The
    output never holds NaN: uncovered pixels are 0, the declared nodata.

    All inputs are first warped (nearest neighbour) onto the union grid of
    the first input's CRS and resolution.  Inputs in the same UTM zone as the
    first one are plain copies, thanks to ``alignToStandardGrid`` in the
    graphs.  But the zone is chosen per graph run (``AUTO:42001``), so two
    sub-swaths can land in different zones near a zone boundary: the tiles
    of the other zone are then resampled once more, with no error or
    warning — values kept, pixels moved by up to half a pixel, a few
    isolated ones picked twice or not at all — and the output takes the
    first input's zone.  See *UTM zone* in the README.  Everything is held
    in memory: for N inputs of B bands over an H x W union, N x B x H x W
    float32.

    If only one input is given the file is copied as-is.

    Args:
        inputs (list[str]): Ordered list of GeoTIFF paths to mosaic.  Band
            layout, names and nodata (0.0) are taken from the first one.
        output (str): Output GeoTIFF path.

    Returns:
        str: Path of the written output file (same as ``output``).

    Raises:
        RuntimeError: If GDAL fails to build the mosaic.
        ImportError: If osgeo.gdal or scipy is not available.
    """
    if not inputs:
        raise ValueError("inputs must not be empty")

    output = Path(output)
    output.resolve().parent.mkdir(parents=True, exist_ok=True)

    if len(inputs) == 1:
        shutil.copy2(inputs[0], output)
        return str(output)

    try:
        from osgeo import gdal
    except ImportError:
        raise ImportError("osgeo.gdal is required for mosaicking")
    import numpy as np
    from scipy import ndimage

    gdal.UseExceptions()
    inputs = [str(p) for p in inputs]

    # Union grid: let Warp work out the extent of all inputs in the CRS and
    # resolution of the first one, without writing anything yet.
    union = gdal.Warp("", inputs, format="MEM", resampleAlg="near",
                      srcNodata=0.0, dstNodata=0.0)
    if union is None:
        raise RuntimeError(f"Mosaic failed -> {str(output)!r}")
    gt, proj = union.GetGeoTransform(), union.GetProjection()
    width, height, n_bands = union.RasterXSize, union.RasterYSize, union.RasterCount
    x_min, y_max = gt[0], gt[3]
    x_max, y_min = x_min + width * gt[1], y_max + height * gt[5]
    union = None

    # Each input on that grid, plus its distance-to-nodata map
    stack, dist = [], []
    for path in inputs:
        ds = gdal.Warp("", path, format="MEM", resampleAlg="near",
                       outputBounds=(x_min, y_min, x_max, y_max),
                       xRes=abs(gt[1]), yRes=abs(gt[5]), dstSRS=proj,
                       srcNodata=0.0, dstNodata=0.0)
        arr = ds.ReadAsArray().astype(np.float32)      # (bands, H, W)
        ds = None
        if arr.ndim == 2:
            arr = arr[None]
        # Valid = every band finite and non-zero.  SNAP leaves NaN (not 0)
        # in the gamma0 bands along the swath edge — a 1-px line that Warp's
        # srcNodata=0 would happily copy — and the coherence window zeroes a
        # wider fringe than the backscatter one: a pixel counts only where all
        # bands are usable, so the other swath's interior wins there.
        valid = np.isfinite(arr).all(axis=0) & (arr != 0).all(axis=0)
        # Distance (pixels) to the nearest invalid pixel; 0 where invalid
        dist.append(ndimage.distance_transform_edt(valid))
        stack.append(arr)

    dist = np.stack(dist)                               # (N, H, W)
    stack = np.stack(stack)                             # (N, bands, H, W)
    best = np.argmax(dist, axis=0)                      # (H, W): winning input
    mosaic = np.take_along_axis(stack, best[None, None], axis=0)[0]
    mosaic[:, dist.max(axis=0) == 0] = 0.0              # covered by no input: nodata, never NaN

    # Write, carrying over band names and the nodata declaration
    src = gdal.Open(inputs[0])
    driver = gdal.GetDriverByName("GTiff")
    out = driver.Create(str(output), width, height, n_bands, gdal.GDT_Float32)
    out.SetGeoTransform(gt)
    out.SetProjection(proj)
    for i in range(n_bands):
        band = out.GetRasterBand(i + 1)
        band.WriteArray(mosaic[i])
        band.SetDescription(src.GetRasterBand(i + 1).GetDescription())
        band.SetNoDataValue(0.0)
    out.FlushCache()
    out = src = None
    return str(output)


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

DEFAULT_PROG = "python snap_gpt.py"

AOI_HELP = (
    "[required] Area of interest in lon/lat WGS84: an inline WKT polygon "
    '(must be quoted: --aoi "POLYGON ((-54.1 4.1, ...))") or a path to a '
    "WKT / GeoJSON file"
)


def _build_parser(prog=None) -> argparse.ArgumentParser:
    # `prog` is how the user invoked the tool — "python rosarium.py gpt" from
    # the repository entry point — so that usage and examples match
    prog = prog or DEFAULT_PROG
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Run one SNAP GPT graph of features/snap_gpt/graphs/ on Sentinel-1 products.\n\n"
            "These are the building blocks of the pre/post pipelines (rosarium.py pre_post),\n"
            "exposed one by one for step-by-step runs and debugging.\n\n"
            "SLC steps (chained by `pre_post backscatter_coherence`):\n"
            "  backscatter      Gamma0 backscatter stack via cross-correlation coregistration\n"
            "  coherence        coherence via ESD coregistration\n"
            "  gathering        collocate backscatter + coherence into pre/post GeoTIFFs\n"
            "  mosaic           merge per-subswath GeoTIFFs into one (no GPT involved)\n\n"
            "GRD step (what `pre_post backscatter` runs):\n"
            "  backscatter-grd  Gamma0 backscatter from two GRD products (no subswath split)\n\n"
            f"Outputs go under data/preprocessed/pre_post/ (intermediates in temp/).\n"
            f"SNAP's gpt: {DEFAULT_GPT or 'NOT FOUND - install SNAP or pass --gpt'}"
        ),
        epilog=(
            "examples:\n"
            f"  {prog} backscatter --input1 pre2.SAFE --input2 post1.SAFE --aoi aoi.geojson\n"
            f"  {prog} coherence --input1 pre1.SAFE --input2 pre2.SAFE --aoi aoi.geojson --pair pre\n"
            f"  {prog} gathering --input-backscatter temp/backscatter.dim "
            "--input-coh-pre temp/coherence_pre.dim --input-coh-post temp/coherence_post.dim --output zta1\n"
            f"  {prog} backscatter-grd --pre pre.SAFE --post post.SAFE --aoi aoi.geojson --output zta1\n"
            f"  {prog} mosaic --inputs zta1_IW2_pre.tif zta1_IW3_pre.tif --output zta1_pre.tif\n"
            f"  {prog} <step> --help   the options of one step"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    common = argparse.ArgumentParser(add_help=False)
    add_gpt_options(common)

    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<step>")

    # -- backscatter ---------------------------------------------------------
    p_bs = subparsers.add_parser(
        "backscatter",
        parents=[common],
        help="Gamma0 backscatter stack via cross-correlation coregistration",
        description=(
            "Process two Sentinel-1 SLC acquisitions through orbit correction, "
            "TOPSAR split, thermal noise removal, radiometric calibration, deburst, "
            "cross-correlation coregistration, speckle filtering, and terrain correction.\n\n"
            "The subswath and burst range are determined automatically from --aoi.\n"
            "If the AOI spans multiple subswaths, the graph runs once per subswath\n"
            "and each output is suffixed with the subswath name (e.g. backscatter_IW2.dim).\n\n"
            "Pass input1=pre2 and input2=post1 so that the master bands (pre2) appear\n"
            "first in the output stack, as required by the gathering step."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_bs.add_argument("--input1", required=True, metavar="PATH",
                      help="[required] Master SLC product - should be pre2 (.zip or .SAFE)")
    p_bs.add_argument("--input2", required=True, metavar="PATH",
                      help="[required] Secondary SLC product - should be post1 (.zip or .SAFE)")
    p_bs.add_argument("--aoi", required=True, metavar="WKT_OR_FILE",
                      help=AOI_HELP + ".  Used to locate the correct subswath/burst range "
                      "and to clip the output.")
    p_bs.add_argument("--output", default=None, metavar="PATH",
                      help=(
                          "[optional] Output product path (.dim).  "
                          "Defaults to data/preprocessed/pre_post/temp/backscatter[_IWx].dim."
                      ))

    # -- coherence -----------------------------------------------------------
    p_coh = subparsers.add_parser(
        "coherence",
        parents=[common],
        help="Interferometric coherence via ESD coregistration",
        description=(
            "Process two Sentinel-1 SLC acquisitions through orbit correction, "
            "TOPSAR split, back-geocoding, Enhanced Spectral Diversity, coherence "
            "estimation, deburst, terrain correction, and spatial clipping.\n\n"
            "The subswath and burst range are determined automatically from --aoi.\n"
            "If the AOI spans multiple subswaths the graph runs once per subswath\n"
            "and each output is suffixed with its name (e.g. coherence_pre_IW2.dim).\n"
            "A subswath reduced to a single burst is processed with\n"
            "coherence_one_burst.xml (same graph without Enhanced-Spectral-Diversity,\n"
            "which needs the overlap between two consecutive bursts).\n\n"
            "Two output modes:\n"
            "  No --pair : standalone run -> data/preprocessed/pre_post/default/coherence[_IWx].dim\n"
            "              (or the path given with --output)\n"
            "  --pair pre/post : pipeline run -> data/preprocessed/pre_post/temp/coherence_pre[_IWx].dim\n"
            "              Use this mode when the output will be fed into gathering."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_coh.add_argument("--input1", required=True, metavar="PATH",
                       help="[required] Master SLC product (.zip or .SAFE)")
    p_coh.add_argument("--input2", required=True, metavar="PATH",
                       help="[required] Secondary SLC product (.zip or .SAFE)")
    p_coh.add_argument("--aoi", required=True, metavar="WKT_OR_FILE", help=AOI_HELP)
    p_coh.add_argument("--pair", default=None, choices=["pre", "post"],
                       help=(
                           "[optional] Event period: 'pre' (pre1+pre2) or 'post' (post1+post2).  "
                           "When given, the output goes to data/preprocessed/pre_post/temp/ with "
                           "a _pre/_post suffix so that gathering can locate it.  "
                           "Omit for a standalone coherence run."
                       ))
    p_coh.add_argument("--output", default=None, metavar="NAME_OR_PATH",
                       help=(
                           "[optional] With --pair: base name in the temp folder "
                           "(e.g. 'zta1' -> temp/zta1_pre.dim). "
                           "Without --pair: full output path "
                           "(default: data/preprocessed/pre_post/default/coherence.dim). "
                           "In both modes, if the AOI spans multiple subswaths the "
                           "subswath name is inserted before the extension "
                           "(e.g. coherence_IW1.dim, coherence_IW2.dim)."
                       ))

    # -- gathering -----------------------------------------------------------
    p_ga = subparsers.add_parser(
        "gathering",
        parents=[common],
        help="Collocate backscatter and coherence stacks into pre/post GeoTIFFs",
        description=(
            "Collocate a backscatter stack with a pre-event and a post-event "
            "coherence product, then split the result into two GeoTIFF files.\n\n"
            "Intended for use after running backscatter + coherence --pair pre + "
            "coherence --pair post on the same AOI.  Band assignment is derived "
            "automatically from the BEAM-DIMAP band names."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_ga.add_argument("--input-backscatter", required=True, metavar="PATH",
                      help="[required] Backscatter stack (.dim), output of the backscatter graph")
    p_ga.add_argument("--input-coh-pre", required=True, metavar="PATH",
                      help="[required] Pre-event coherence (.dim), output of coherence --pair pre")
    p_ga.add_argument("--input-coh-post", required=True, metavar="PATH",
                      help="[required] Post-event coherence (.dim), output of coherence --pair post")
    p_ga.add_argument("--output", default=None, metavar="NAME_OR_PATH",
                      help=(
                          "[optional] Run name: creates data/preprocessed/pre_post/<name>/ and writes "
                          "<name>_pre.tif and <name>_post.tif inside it.  A path prefix "
                          "(.../zta6/zta6_slc) writes zta6_slc_pre.tif / _post.tif in that folder.  "
                          "Defaults to data/preprocessed/pre_post/default/pre.tif and post.tif."
                      ))

    # -- backscatter-grd -----------------------------------------------------
    p_grd = subparsers.add_parser(
        "backscatter-grd",
        parents=[common],
        help="Gamma0 backscatter from two GRD products (no subswath split required)",
        description=(
            "Process two Sentinel-1 GRD acquisitions through orbit correction, thermal\n"
            "noise removal, border noise removal, radiometric calibration, cross-correlation\n"
            "coregistration, speckle filtering, terrain correction, and spatial clipping.\n\n"
            "Both images are processed together in a single graph run.  The output is split\n"
            "into two GeoTIFFs: <name>_pre.tif (master/pre image) and <name>_post.tif\n"
            "(slave/post image), each with gamma0_VH and gamma0_VV bands.\n\n"
            "Unlike the SLC pipeline there is no subswath/burst detection step - GRD products\n"
            "already cover the full swath."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_grd.add_argument("--pre", required=True, metavar="PATH",
                       help="[required] Pre-event GRD product (.zip or .SAFE) - used as master")
    p_grd.add_argument("--post", required=True, metavar="PATH",
                       help="[required] Post-event GRD product (.zip or .SAFE) - used as slave")
    p_grd.add_argument("--aoi", required=True, metavar="WKT_OR_FILE", help=AOI_HELP)
    p_grd.add_argument("--output", default=None, metavar="NAME_OR_PATH",
                       help=(
                           "[optional] Run name: creates data/preprocessed/pre_post/<name>/ and writes "
                           "<name>_pre.tif and <name>_post.tif inside it.  A path prefix "
                           "(.../zta6/zta6_grd) writes zta6_grd_pre.tif / _post.tif in that folder.  "
                           "If omitted, writes pre.tif and post.tif to data/preprocessed/pre_post/default/."
                       ))

    # -- mosaic --------------------------------------------------------------
    # No GPT here, so no --gpt / memory flags: not built on `common`.
    p_mo = subparsers.add_parser(
        "mosaic",
        help="Merge per-subswath GeoTIFFs (same bands) into a single file",
        description=(
            "Merge two or more GeoTIFFs with the same bands - typically the per-subswath\n"
            "outputs of gathering (<name>_IW1_pre.tif, <name>_IW2_pre.tif, ...) - into one.\n\n"
            "Where inputs overlap, each pixel is taken from the input in which it lies\n"
            "farthest from an invalid pixel, so the degraded swath edges (a 1-px NaN line\n"
            "in gamma0, a zeroed fringe in coherence) are replaced by the neighbour's\n"
            "interior instead of being painted over it.  Band names and nodata (0) are\n"
            "carried over from the first input.  Everything is held in memory."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_mo.add_argument("--inputs", required=True, nargs="+", metavar="PATH",
                      help="[required] Two or more GeoTIFFs to merge, e.g. zta_IW2_pre.tif zta_IW3_pre.tif")
    p_mo.add_argument("--output", required=True, metavar="PATH",
                      help="[required] Output GeoTIFF path")

    return parser


def main(argv=None, prog=None):
    parser = _build_parser(prog)
    args = parser.parse_args(argv)

    if args.command == "mosaic":
        print(run_mosaic(args.inputs, args.output))
        return 0

    gpt_options = gpt_options_from_args(args)

    if args.command == "backscatter":
        ok = run_backscatter(
            input1=args.input1,
            input2=args.input2,
            aoi=args.aoi,
            output=args.output,
            gpt_path=args.gpt,
            gpt_options=gpt_options,
        )
        return 0 if all(ok) else 1
    if args.command == "coherence":
        ok = run_coherence(
            input1=args.input1,
            input2=args.input2,
            aoi=args.aoi,
            pair=args.pair,
            output=args.output,
            gpt_path=args.gpt,
            gpt_options=gpt_options,
        )
        return 0 if all(ok) else 1
    if args.command == "gathering":
        tifs = run_gathering(
            input_backscatter=args.input_backscatter,
            input_coh_pre=args.input_coh_pre,
            input_coh_post=args.input_coh_post,
            output=args.output,
            gpt_path=args.gpt,
            gpt_options=gpt_options,
        )
    else:  # backscatter-grd
        tifs = run_backscatter_grd(
            pre=args.pre,
            post=args.post,
            aoi=args.aoi,
            output=args.output,
            gpt_path=args.gpt,
            gpt_options=gpt_options,
        )
    for path in tifs:
        print(path)
    return 0 if len(tifs) == 2 else 1


if __name__ == "__main__":
    sys.exit(main())
