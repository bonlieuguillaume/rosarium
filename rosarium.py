"""rosarium — one entry point for the tools of this repository.

    python rosarium.py webmap --open              # the webmap in the browser
    python rosarium.py download                   # the listed products, with rclone
    python rosarium.py slc --pre1 ... --output x  # four SLC -> pre/post gamma0 + coherence
    python rosarium.py grd --pre ... --output x   # two GRD  -> pre/post gamma0
    python rosarium.py gpt <step> ...             # one SNAP graph at a time
    python rosarium.py bursts --slc-path ... --polygon ...
    python rosarium.py --help                     # the list below
    python rosarium.py <command> --help           # the options of one command

The first word picks the command; what follows is handed to that command's own
parser, so each tool keeps its options and can still be run from its own file.
A new tool with a command-line use adds one entry to `COMMANDS`.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROG = "python rosarium.py"

# command -> (module holding `main(argv, prog)`, one-line description)
COMMANDS = {
    "webmap": ("frontend.server", "serve the webmap in the browser (AOI -> Sentinel-1 products)"),
    "download": (
        "features.download_products.download_products",
        "download the products of a path file from CDSE with rclone (-> data/raw/<folder>)",
    ),
    "slc": (
        "features.pre_post.pre_post_backscatter_coherence.pre_post_backscatter_coherence",
        "four SLC (2 pre, 2 post) -> pre/post GeoTIFFs of gamma0 + coherence, through SNAP",
    ),
    "grd": (
        "features.pre_post.pre_post_backscatter.pre_post_backscatter",
        "two GRD (pre, post) -> pre/post GeoTIFFs of gamma0, through SNAP",
    ),
    "gpt": (
        "features.snap_gpt.snap_gpt",
        "one SNAP graph at a time: backscatter, coherence, gathering, backscatter-grd, mosaic",
    ),
    "bursts": (
        "features.polygon_to_swaths_bursts.polygon_to_swaths_bursts",
        "which sub-swaths and bursts of a Sentinel-1 SLC product a polygon intersects",
    ),
}


def usage():
    width = max(len(c) for c in COMMANDS)
    lines = [f"usage: {PROG} <command> [options]", "", "commands:"]
    lines += [f"  {name:<{width}}  {desc}" for name, (_, desc) in COMMANDS.items()]
    lines += ["", f"{PROG} <command> --help shows the options of a command."]
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(usage())
        return 0
    command, rest = argv[0], argv[1:]
    if command not in COMMANDS:
        print(f"unknown command {command!r}\n\n{usage()}", file=sys.stderr)
        return 2
    module_name, _ = COMMANDS[command]
    # Imported on demand: each command pulls its own dependencies, and the
    # help must work even when a tool's packages are missing
    module = __import__(module_name, fromlist=["main"])
    return module.main(rest, prog=f"{PROG} {command}")


if __name__ == "__main__":
    sys.exit(main())
