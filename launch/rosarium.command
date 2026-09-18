#!/usr/bin/env bash
# rosarium webmap launcher (macOS): the Finder runs a .command file in the
# Terminal on double-click — the macOS counterpart of rosarium.bat. It only
# hands over to rosarium.sh. Needs to be executable once: chmod +x launch/*.command
# (make_shortcut.sh does it).
exec bash "$(cd "$(dirname "$0")" && pwd -P)/rosarium.sh" "$@"
