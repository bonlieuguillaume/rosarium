#!/usr/bin/env bash
# Creates (or replaces) a desktop entry for rosarium.sh: in the applications
# menu (~/.local/share/applications) and on the desktop. Run it once; run it
# again after renaming the entry below or changing the icon.
#
#   name : SHORTCUT_NAME below (the Name= field is what the desktop shows)
#   icon : launch/rosarium.svg when it exists (sharp at every size), else
#          launch/rosarium.png (safe everywhere), else a generic icon.
#
# GNOME may ask, on first double-click, to "Allow launching" the desktop copy.

set -euo pipefail
SHORTCUT_NAME="rosarium"
LAUNCH="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

ICON="utilities-terminal"   # a theme icon, when no logo is shipped
for f in "$LAUNCH/rosarium.svg" "$LAUNCH/rosarium.png"; do
    if [ -f "$f" ]; then ICON="$f"; break; fi
done

chmod +x "$LAUNCH/rosarium.sh"

ENTRY="[Desktop Entry]
Type=Application
Name=$SHORTCUT_NAME
Comment=rosarium webmap
Exec=$LAUNCH/rosarium.sh
Path=$LAUNCH/..
Icon=$ICON
Terminal=true
Categories=Science;Geography;
"

APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
printf '%s' "$ENTRY" > "$APPS/$SHORTCUT_NAME.desktop"
echo "  menu entry created: $APPS/$SHORTCUT_NAME.desktop"

DESKTOP="$(command -v xdg-user-dir >/dev/null 2>&1 && xdg-user-dir DESKTOP || echo "$HOME/Desktop")"
if [ -d "$DESKTOP" ]; then
    printf '%s' "$ENTRY" > "$DESKTOP/$SHORTCUT_NAME.desktop"
    chmod +x "$DESKTOP/$SHORTCUT_NAME.desktop"
    echo "  desktop entry created: $DESKTOP/$SHORTCUT_NAME.desktop"
fi
[ "$ICON" = "utilities-terminal" ] && echo "  no launch/rosarium.svg or .png: generic icon used"
exit 0
