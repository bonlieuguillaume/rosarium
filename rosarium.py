"""rosarium — one entry point for the tools of this repository.

    python rosarium.py webmap --open                          # the webmap in the browser
    python rosarium.py download                               # the listed products, with rclone
    python rosarium.py pre_post backscatter_coherence ...     # four SLC -> pre/post gamma0 + coherence
    python rosarium.py pre_post backscatter ...               # two GRD  -> pre/post gamma0
    python rosarium.py gpt <step> ...                         # one SNAP graph at a time
    python rosarium.py bursts --slc-path ... --polygon ...
    python rosarium.py --help                                 # the list below
    python rosarium.py <command> --help                       # the options of one command

The first word picks the command; what follows is handed to that command's own
parser, so each tool keeps its options and can still be run from its own file.
A command can also be a group — `pre_post` holds the pipelines of
`features/pre_post/` — whose second word picks the tool. A new tool with a
command-line use adds one entry to `COMMANDS`, or to a group of it.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROG = "python rosarium.py"

# name -> (target, one-line description). The target is either the module
# holding `main(argv, prog)`, or a dict of the same shape for a group of
# sub-commands (`python rosarium.py <group> <sub-command> ...`).
COMMANDS = {
    "webmap": ("frontend.server", "serve the webmap in the browser (AOI -> Sentinel-1 products)"),
    "download": (
        "features.download_products.download_products",
        "download the products of a path file from CDSE with rclone (-> data/raw/<folder>)",
    ),
    "pre_post": (
        {
            "backscatter_coherence": (
                "features.pre_post.pre_post_backscatter_coherence.pre_post_backscatter_coherence",
                "four SLC (2 pre, 2 post) -> gamma0 + coherence",
            ),
            "backscatter": (
                "features.pre_post.pre_post_backscatter.pre_post_backscatter",
                "two GRD (pre, post) -> gamma0",
            ),
        },
        "pre/post GeoTIFFs around an event, through SNAP",
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


def _command_lines(commands, indent=2):
    """One line per command, a group's sub-commands indented under it."""
    width = max(len(name) for name in commands)
    lines = []
    for name, (target, desc) in commands.items():
        lines.append(f"{' ' * indent}{name:<{width}}  {desc}")
        if isinstance(target, dict):
            lines += _command_lines(target, indent + 4)
    return lines


def usage(prog=PROG, commands=COMMANDS):
    what = "<command>" if commands is COMMANDS else "<sub-command>"
    lines = [f"usage: {prog} {what} [options]", "", f"{what[1:-1]}s:"]
    lines += _command_lines(commands)
    lines += ["", f"{prog} {what} --help shows the options of one."]
    return "\n".join(lines)


def main(argv=None, prog=PROG, commands=COMMANDS):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(usage(prog, commands))
        return 0
    name, rest = argv[0], argv[1:]
    if name not in commands:
        print(f"unknown command {name!r}\n\n{usage(prog, commands)}", file=sys.stderr)
        return 2
    target, _ = commands[name]
    if isinstance(target, dict):
        # A group: the next word picks the tool, same rules one level down
        return main(rest, prog=f"{prog} {name}", commands=target)
    # Imported on demand: each command pulls its own dependencies, and the
    # help must work even when a tool's packages are missing
    module = __import__(target, fromlist=["main"])
    return module.main(rest, prog=f"{prog} {name}")


if __name__ == "__main__":
    sys.exit(main())
