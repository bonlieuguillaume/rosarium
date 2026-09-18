#!/usr/bin/env bash
# rosarium webmap launcher (Linux / macOS): bash launch/rosarium.sh, or the
# desktop entry / app made by make_shortcut.sh. Activates the conda env and
# starts "python rosarium.py webmap --open". Ctrl+C stops the server. Extra
# arguments are passed on: rosarium.sh --port 9000

set -euo pipefail
ENV_NAME="rosarium"

# Resolve this script's real location, through symlinks, without readlink -f
# (missing on macOS before 12.3)
resolve() {
    local p="$1"
    while [ -L "$p" ]; do
        local d; d="$(cd "$(dirname "$p")" && pwd -P)"
        p="$(readlink "$p")"; [[ "$p" != /* ]] && p="$d/$p"
    done
    echo "$(cd "$(dirname "$p")" && pwd -P)/$(basename "$p")"
}
LAUNCH="$(dirname "$(resolve "$0")")"

# Find the conda base install: miniforge first (home, then the Homebrew cask
# locations on macOS), then the classic ones, then whatever conda is on PATH
CONDA_BASE=""
for d in "$HOME/miniforge3" \
         "/opt/homebrew/Caskroom/miniforge/base" "/usr/local/Caskroom/miniforge/base" \
         "$HOME/miniconda3" "$HOME/anaconda3" "/opt/miniconda3" "/opt/anaconda3" "/opt/conda"; do
    if [ -f "$d/etc/profile.d/conda.sh" ]; then CONDA_BASE="$d"; break; fi
done
if [ -z "$CONDA_BASE" ] && command -v conda >/dev/null 2>&1; then
    CONDA_BASE="$(conda info --base)"
fi
if [ -z "$CONDA_BASE" ]; then
    echo "Could not find a conda install: set CONDA_BASE in launch/rosarium.sh" >&2
    read -rp "Press Enter to close"; exit 1
fi

# Run from the repository root (this file sits in launch/)
cd "$LAUNCH/.."

# shellcheck disable=SC1091
source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate "$ENV_NAME" || { echo "Could not activate the conda env $ENV_NAME" >&2; read -rp "Press Enter to close"; exit 1; }

python rosarium.py webmap --open "$@" || read -rp "Press Enter to close"
