"""Pre/post backscatter GeoTIFFs from two Sentinel-1 GRD products.

One acquisition before an event and one after it go through the GRD graph of
`features/snap_gpt/` — orbit, noise removal, calibration, coregistration,
speckle filter, terrain correction, clip — and come out as two GeoTIFFs,
`<name>_pre.tif` and `<name>_post.tif`, each holding gamma0_VH and gamma0_VV.

    python rosarium.py grd --pre ... --post ... --aoi aoi.geojson --output zta1

Usable as a library too: `main_preprocess_grd(...)` returns the two paths.
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
    GptOptions,
    _is_path,
    add_gpt_options,
    gpt_options_from_args,
    run_backscatter_grd,
)


def main_preprocess_grd(
    pre: str,
    post: str,
    aoi: str,
    output_name: str,
    gpt_path: str = DEFAULT_GPT,
    gpt_options: GptOptions = DEFAULT_GPT_OPTIONS,
) -> dict:
    """
    GRD-only pre/post preprocessing pipeline.

    Runs a single SNAP graph on two Sentinel-1 GRD products and produces two
    final GeoTIFFs: one pre-event and one post-event, each containing Gamma0
    backscatter bands (VH and VV).

    Unlike the SLC pipeline, no subswath detection is needed — GRD products
    already cover the full swath.  Both images are processed together through
    orbit correction, thermal noise removal, border noise removal, radiometric
    calibration, cross-correlation coregistration, speckle filtering, terrain
    correction, and spatial clipping.

    Image roles:
        pre  — pre-event acquisition (master for coregistration)
        post — post-event acquisition (slave)

    Processing steps:
        1. Run the backscatter_grd graph (both images in one pass)
        2. Split the coregistered stack into pre and post GeoTIFFs

    Args:
        pre (str): Path to the pre-event Sentinel-1 GRD product (.zip or .SAFE).
        post (str): Path to the post-event Sentinel-1 GRD product (.zip or .SAFE).
        aoi (str): Area of interest in lon/lat WGS84 — inline WKT, or a path
            to a WKT / GeoJSON file.
        output_name (str): Label for this run (e.g. ``"zta1"``), or a path.

            * Simple name (``"zta1"``) — a folder
              ``data/preprocessed/pre_post/zta1/`` is created and the products
              are written inside it.
            * Path (``"data/preprocessed/pre_post/zta6/zta6_grd"``) — the
              products are written inside that directory (created if needed),
              using the last segment (``zta6_grd``) as filename prefix.
        gpt_path (str): Path to the SNAP GPT executable.
        gpt_options (GptOptions): Heap / cache / threads / tile size handed to
            gpt (see the top of ``snap_gpt.py`` for how to choose them).

    Returns:
        dict: ``{"pre": <path>, "post": <path>}`` — absolute paths of the
        two final GeoTIFFs.

            - ``<output_name>_pre.tif``:  gamma0_VH and gamma0_VV from the pre image
            - ``<output_name>_post.tif``: gamma0_VH and gamma0_VV from the post image

    Raises:
        RuntimeError: If the GPT graph fails to produce output files.
    """
    if _is_path(output_name):
        out_dir = Path(output_name).resolve()
        prefix = out_dir.name
    else:
        out_dir = PRE_POST_DIR / output_name
        prefix = output_name
    out_dir.mkdir(parents=True, exist_ok=True)

    gather_prefix = out_dir / prefix
    tifs = run_backscatter_grd(pre, post, aoi, output=str(gather_prefix),
                               gpt_path=gpt_path, gpt_options=gpt_options)

    if len(tifs) < 2:
        raise RuntimeError(
            "GRD preprocessing failed: expected two output GeoTIFFs but got "
            f"{len(tifs)}.  Check GPT logs above for details."
        )

    return {"pre": tifs[0], "post": tifs[1]}


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

DEFAULT_PROG = "python pre_post_backscatter.py"


def _build_parser(prog=None):
    # `prog` is how the user invoked the tool — "python rosarium.py grd" from
    # the repository entry point — so that usage and examples match
    prog = prog or DEFAULT_PROG
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Pre/post backscatter GeoTIFFs from two Sentinel-1 GRD products.\n\n"
            "Runs orbit correction, thermal noise removal, border noise removal,\n"
            "radiometric calibration, cross-correlation coregistration, speckle\n"
            "filtering, terrain correction, and spatial clipping on two Sentinel-1\n"
            "GRD acquisitions (one SNAP graph, the same as `rosarium.py gpt backscatter-grd`).\n\n"
            "Image roles:\n"
            "  pre  - pre-event acquisition (master for coregistration)\n"
            "  post - post-event acquisition (slave)\n\n"
            "Outputs (written to data/preprocessed/pre_post/<NAME>/):\n"
            "  <NAME>_pre.tif  - pre-event product\n"
            "                    bands: gamma0_VH (pre), gamma0_VV (pre)\n"
            "  <NAME>_post.tif - post-event product\n"
            "                    bands: gamma0_VH (post), gamma0_VV (post)\n\n"
            "If --output is a path instead of a simple name, the products are\n"
            "written inside that directory (created if needed) and its last\n"
            "segment is used as filename prefix, e.g.:\n"
            "  --output data/preprocessed/pre_post/zta6/zta6_grd\n"
            "  -> data/preprocessed/pre_post/zta6/zta6_grd/zta6_grd_pre.tif and zta6_grd_post.tif\n\n"
            "The intermediate .dim stack lands in data/preprocessed/pre_post/temp/.\n"
            f"SNAP's gpt: {DEFAULT_GPT or 'NOT FOUND - install SNAP or pass --gpt'}"
        ),
        epilog=(
            "example:\n"
            f"  {prog} --pre data/raw/vrac/S1A_..._pre.SAFE --post data/raw/vrac/S1A_..._post.SAFE\n"
            "      --aoi data/utils/list_aoi.geojson --output zta1"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pre", required=True, metavar="PATH",
                        help="[required] Pre-event GRD product (.zip or .SAFE)")
    parser.add_argument("--post", required=True, metavar="PATH",
                        help="[required] Post-event GRD product (.zip or .SAFE)")
    parser.add_argument("--aoi", required=True, metavar="WKT_OR_FILE",
                        help=(
                            "[required] Area of interest in lon/lat WGS84: an inline WKT polygon "
                            '(must be quoted: --aoi "POLYGON ((-54.1 4.1, ...))") or a path to a '
                            "WKT / GeoJSON file.  Used to clip the outputs."
                        ))
    parser.add_argument("--output", required=True, metavar="NAME_OR_PATH",
                        help=(
                            "[required] Run label or output path.  "
                            "Simple name: creates data/preprocessed/pre_post/<NAME>/ and writes "
                            "<NAME>_pre.tif and <NAME>_post.tif inside it.  "
                            "Path (e.g. data/preprocessed/pre_post/zta6/zta6_grd): creates that "
                            "directory if needed and writes zta6_grd_pre.tif and "
                            "zta6_grd_post.tif inside it."
                        ))
    add_gpt_options(parser)
    return parser


def main(argv=None, prog=None):
    parser = _build_parser(prog)
    args = parser.parse_args(argv)
    result = main_preprocess_grd(
        pre=args.pre,
        post=args.post,
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
