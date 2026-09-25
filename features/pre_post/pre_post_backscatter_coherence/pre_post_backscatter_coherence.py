"""Pre/post backscatter + coherence GeoTIFFs from four Sentinel-1 SLC products.

Two acquisitions before an event and two after it (pre1, pre2, post1, post2)
go through the SNAP graphs of `features/snap_gpt/` — backscatter stack,
pre-event coherence, post-event coherence, gathering, mosaic when the AOI
spans several sub-swaths — and come out as two GeoTIFFs, `<name>_pre.tif`
and `<name>_post.tif`, each holding gamma0_VH, gamma0_VV, coh_VH, coh_VV.

    python rosarium.py pre_post backscatter_coherence --pre1 ... --pre2 ... --post1 ... --post2 ... --aoi aoi.geojson --output zta1

Usable as a library too: `main_preprocess(...)` returns the two paths.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:  # so the module also runs from its own folder
    sys.path.insert(0, str(ROOT))

from features.snap_gpt.snap_gpt import (  # noqa: E402
    DEFAULT_GPT,
    DEFAULT_GPT_OPTIONS,
    PRE_POST_DIR,
    TEMP_DIR,
    GptOptions,
    _is_path,
    add_gpt_options,
    gpt_options_from_args,
    polygon_to_swaths_bursts,
    run_backscatter,
    run_coherence,
    run_gathering,
    run_mosaic,
)


def main_preprocess(
    pre1: str,
    pre2: str,
    post1: str,
    post2: str,
    aoi: str,
    output_name: str,
    gpt_path: str = DEFAULT_GPT,
    gpt_options: GptOptions = DEFAULT_GPT_OPTIONS,
) -> dict:
    """
    Full SLC pre/post preprocessing pipeline.

    Runs all graphs in the correct order and produces two final GeoTIFFs:
    one pre-event product and one post-event product, each containing
    Gamma0 backscatter and interferometric coherence bands.

    Image roles:
        pre1  — earliest acquisition (coherence reference for pre pair)
        pre2  — second acquisition  (coherence secondary for pre pair;
                                     master for backscatter stack)
        post1 — third acquisition   (slave for backscatter stack;
                                     coherence reference for post pair)
        post2 — latest acquisition  (coherence secondary for post pair)

    Processing steps:
        1. Detect subswath(s) and burst range from AOI
        2. Backscatter stack  : pre2 x post1
        3. Pre-event coherence: pre1 x pre2
        4. Post-event coherence: post1 x post2
        5. Gathering per subswath -> per-swath GeoTIFFs
        6. Mosaic  (only when the AOI spans multiple subswaths)

    Args:
        pre1 (str): Earliest SLC product (.zip or .SAFE).
        pre2 (str): Second SLC product.
        post1 (str): Third SLC product.
        post2 (str): Latest SLC product.
        aoi (str): Area of interest in lon/lat WGS84 — inline WKT, or a path
            to a WKT / GeoJSON file.
        output_name (str): Label for this run (e.g. ``"zta1"``), or a path.

            * Simple name (``"zta1"``) — a folder
              ``data/preprocessed/pre_post/zta1/`` is created and the products
              are written inside it.
            * Path (``"data/preprocessed/pre_post/zta6/zta6_slc"``) — the
              products are written inside that directory (created if needed),
              using the last segment (``zta6_slc``) as filename prefix.
        gpt_path (str): Path to the SNAP GPT executable.
        gpt_options (GptOptions): Heap / cache / threads / tile size handed to
            every gpt call (see the top of ``snap_gpt.py`` for how to choose
            them).

    Returns:
        dict: ``{"pre": <path>, "post": <path>}`` — absolute paths of the
        two final GeoTIFFs.

    Raises:
        ValueError: If no subswath intersects the AOI.
    """
    # 1. Detect subswaths once (pre2 as reference product). Coarse mode: an
    # AOI on a burst seam also gets the neighbouring burst — a missing burst
    # would leave a silent nodata hole in the final GeoTIFFs, an extra one
    # costs seconds (see polygon_to_swaths_bursts in snap_gpt).
    swaths = polygon_to_swaths_bursts(pre2, aoi)
    if not swaths:
        raise ValueError(f"No subswath intersects the given AOI in {pre2!r}")

    multi = len(swaths) > 1

    # 2-4. Per-pair processing (each function loops over swaths internally)
    run_backscatter(pre2, post1, aoi, gpt_path=gpt_path, gpt_options=gpt_options)
    run_coherence(pre1, pre2, aoi, pair="pre", gpt_path=gpt_path, gpt_options=gpt_options)
    run_coherence(post1, post2, aoi, pair="post", gpt_path=gpt_path, gpt_options=gpt_options)

    # 5. Gathering — all outputs go into the same output folder
    if _is_path(output_name):
        out_dir = Path(output_name).resolve()
        prefix = out_dir.name
    else:
        out_dir = PRE_POST_DIR / output_name
        prefix = output_name
    out_dir.mkdir(parents=True, exist_ok=True)

    pre_tifs: list[str] = []
    post_tifs: list[str] = []

    for swath in swaths:
        iw = swath["subswath"]
        suffix = f"_{iw}" if multi else ""

        bs_path = TEMP_DIR / f"backscatter{suffix}.dim"
        coh_pre_path = TEMP_DIR / f"coherence_pre{suffix}.dim"
        coh_post_path = TEMP_DIR / f"coherence_post{suffix}.dim"

        # Pass a full path prefix so run_gathering writes directly into out_dir
        # (single-swath: zta1/zta1, multi-swath: zta1/zta1_IW1, zta1/zta1_IW2)
        gather_prefix = out_dir / f"{prefix}{suffix}"
        tifs = run_gathering(str(bs_path), str(coh_pre_path), str(coh_post_path),
                             output=str(gather_prefix), gpt_path=gpt_path,
                             gpt_options=gpt_options)

        if len(tifs) >= 1:
            pre_tifs.append(tifs[0])
        if len(tifs) >= 2:
            post_tifs.append(tifs[1])

    if len(pre_tifs) < len(swaths) or len(post_tifs) < len(swaths):
        raise RuntimeError(
            "SLC preprocessing failed: gathering did not produce both GeoTIFFs "
            "for every sub-swath.  Check the GPT logs above for details."
        )

    # 6. Single-swath: gathering already wrote the final files
    if not multi:
        return {"pre": pre_tifs[0], "post": post_tifs[0]}

    # Multi-swath: mosaic the per-swath tiles (all already in out_dir)
    final_pre = out_dir / f"{prefix}_pre.tif"
    final_post = out_dir / f"{prefix}_post.tif"

    run_mosaic(pre_tifs, str(final_pre))
    run_mosaic(post_tifs, str(final_post))

    return {"pre": str(final_pre), "post": str(final_post)}


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

DEFAULT_PROG = "python pre_post_backscatter_coherence.py"


def _build_parser(prog=None):
    # `prog` is how the user invoked the tool — "python rosarium.py pre_post
    # backscatter_coherence" from the repository entry point — so that usage
    # and examples match
    prog = prog or DEFAULT_PROG
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Pre/post backscatter + coherence GeoTIFFs from four Sentinel-1 SLC products.\n\n"
            "Runs the SNAP graphs backscatter, coherence (pre + post), gathering and\n"
            "mosaic in the correct order (each is also reachable alone through\n"
            "`rosarium.py gpt <step>`).  Two acquisitions before the event, two after:\n\n"
            "Image roles:\n"
            "  pre1  - earliest acquisition\n"
            "  pre2  - second acquisition  (master for backscatter)\n"
            "  post1 - third acquisition   (slave for backscatter)\n"
            "  post2 - latest acquisition\n\n"
            "Outputs (written to data/preprocessed/pre_post/<NAME>/):\n"
            "  <NAME>_pre.tif  - pre-event product\n"
            "                    bands: gamma0_VH (pre2), gamma0_VV (pre2),\n"
            "                           coh_VH (pre1 x pre2), coh_VV (pre1 x pre2)\n"
            "  <NAME>_post.tif - post-event product\n"
            "                    bands: gamma0_VH (post1), gamma0_VV (post1),\n"
            "                           coh_VH (post1 x post2), coh_VV (post1 x post2)\n\n"
            "If --output is a path instead of a simple name, the products are\n"
            "written inside that directory (created if needed) and its last\n"
            "segment is used as filename prefix, e.g.:\n"
            "  --output data/preprocessed/pre_post/zta6/zta6_slc\n"
            "  -> data/preprocessed/pre_post/zta6/zta6_slc/zta6_slc_pre.tif and zta6_slc_post.tif\n\n"
            "Intermediate .dim products land in data/preprocessed/pre_post/temp/.\n"
            f"SNAP's gpt: {DEFAULT_GPT or 'NOT FOUND - install SNAP or pass --gpt'}"
        ),
        epilog=(
            "example:\n"
            f"  {prog} --pre1 data/raw/vrac/S1A_..._pre1.SAFE --pre2 data/raw/vrac/S1A_..._pre2.SAFE\n"
            "      --post1 data/raw/vrac/S1A_..._post1.SAFE --post2 data/raw/vrac/S1A_..._post2.SAFE\n"
            "      --aoi data/utils/list_aoi.geojson --output zta1 --xmx 10G --cache 3G --threads 4"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pre1", required=True, metavar="PATH", help="[required] Earliest SLC product (.zip or .SAFE)")
    parser.add_argument("--pre2", required=True, metavar="PATH", help="[required] Second SLC product (.zip or .SAFE)")
    parser.add_argument("--post1", required=True, metavar="PATH", help="[required] Third SLC product (.zip or .SAFE)")
    parser.add_argument("--post2", required=True, metavar="PATH", help="[required] Latest SLC product (.zip or .SAFE)")
    parser.add_argument("--aoi", required=True, metavar="WKT_OR_FILE",
                        help=(
                            "[required] Area of interest in lon/lat WGS84: an inline WKT polygon "
                            '(must be quoted: --aoi "POLYGON ((-54.1 4.1, ...))") or a path to a '
                            "WKT / GeoJSON file.  Used to locate the subswath/burst range and "
                            "to clip the outputs."
                        ))
    parser.add_argument("--output", required=True, metavar="NAME_OR_PATH",
                        help=(
                            "[required] Run label or output path.  "
                            "Simple name: creates data/preprocessed/pre_post/<NAME>/ and writes "
                            "<NAME>_pre.tif and <NAME>_post.tif inside it.  "
                            "Path (e.g. data/preprocessed/pre_post/zta6/zta6_slc): creates that "
                            "directory if needed and writes zta6_slc_pre.tif and "
                            "zta6_slc_post.tif inside it."
                        ))
    add_gpt_options(parser)
    return parser


def main(argv=None, prog=None):
    parser = _build_parser(prog)
    args = parser.parse_args(argv)
    result = main_preprocess(
        pre1=args.pre1,
        pre2=args.pre2,
        post1=args.post1,
        post2=args.post2,
        aoi=args.aoi,
        output_name=args.output,
        gpt_path=args.gpt,
        gpt_options=gpt_options_from_args(args),
    )
    print(f"Pre-event product : {result['pre']}")
    print(f"Post-event product: {result['post']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
