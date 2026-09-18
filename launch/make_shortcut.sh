#!/usr/bin/env bash
# Creates (or replaces) a desktop launcher for rosarium.sh. Run it once:
# bash launch/make_shortcut.sh. Run it again after renaming the launcher
# below or changing the icon.
#
#   Linux : a .desktop entry in the applications menu (~/.local/share/applications)
#           and on the desktop. Icon: launch/rosarium.svg, else rosarium.png,
#           else a generic theme icon. GNOME may ask, on first double-click,
#           to "Allow launching" the desktop copy.
#   macOS : a <name>.app bundle on the desktop that opens rosarium.command in
#           the Terminal. Icon: launch/rosarium.png turned into .icns with the
#           system tools (sips, iconutil); no icon without the PNG.
#
#   name : SHORTCUT_NAME below.

set -euo pipefail
SHORTCUT_NAME="Rosarium"
LAUNCH="$(cd "$(dirname "$0")" && pwd -P)"

chmod +x "$LAUNCH/rosarium.sh" "$LAUNCH"/*.command

# --- Linux: .desktop entry ----------------------------------------------------

make_desktop_entry() {
    local icon="utilities-terminal"   # a theme icon, when no logo is shipped
    local f
    for f in "$LAUNCH/rosarium.svg" "$LAUNCH/rosarium.png"; do
        if [ -f "$f" ]; then icon="$f"; break; fi
    done

    local entry="[Desktop Entry]
Type=Application
Name=$SHORTCUT_NAME
Comment=rosarium webmap
Exec=$LAUNCH/rosarium.sh
Path=$LAUNCH/..
Icon=$icon
Terminal=true
Categories=Science;Geography;
"
    local apps="$HOME/.local/share/applications"
    mkdir -p "$apps"
    printf '%s' "$entry" > "$apps/$SHORTCUT_NAME.desktop"
    echo "  menu entry created: $apps/$SHORTCUT_NAME.desktop"

    local desktop
    desktop="$(command -v xdg-user-dir >/dev/null 2>&1 && xdg-user-dir DESKTOP || echo "$HOME/Desktop")"
    if [ -d "$desktop" ]; then
        printf '%s' "$entry" > "$desktop/$SHORTCUT_NAME.desktop"
        chmod +x "$desktop/$SHORTCUT_NAME.desktop"
        echo "  desktop entry created: $desktop/$SHORTCUT_NAME.desktop"
    fi
    [ "$icon" = "utilities-terminal" ] && echo "  no launch/rosarium.svg or .png: generic icon used"
    return 0
}

# --- macOS: .app bundle -------------------------------------------------------

# An .app is a folder: Contents/Info.plist describes it, Contents/MacOS/<name>
# is what runs on double-click, Contents/Resources holds the .icns icon.
make_app_bundle() {
    local app="$HOME/Desktop/$SHORTCUT_NAME.app"
    rm -rf "$app"
    mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"

    # The bundle's executable only opens the .command in the Terminal, so the
    # server has a visible console (Ctrl+C, error messages), like on Windows
    cat > "$app/Contents/MacOS/$SHORTCUT_NAME" <<EOF
#!/usr/bin/env bash
open -a Terminal "$LAUNCH/rosarium.command"
EOF
    chmod +x "$app/Contents/MacOS/$SHORTCUT_NAME"

    local icon_key=""
    if [ -f "$LAUNCH/rosarium.png" ] && command -v sips >/dev/null && command -v iconutil >/dev/null; then
        # .icns wants a folder of PNGs at fixed sizes, then iconutil packs it
        local iconset; iconset="$(mktemp -d)/$SHORTCUT_NAME.iconset"
        mkdir -p "$iconset"
        local size
        for size in 16 32 64 128 256 512; do
            sips -z "$size" "$size" "$LAUNCH/rosarium.png" --out "$iconset/icon_${size}x${size}.png" >/dev/null
            sips -z "$((size * 2))" "$((size * 2))" "$LAUNCH/rosarium.png" --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
        done
        if iconutil -c icns "$iconset" -o "$app/Contents/Resources/$SHORTCUT_NAME.icns"; then
            icon_key="    <key>CFBundleIconFile</key>
    <string>$SHORTCUT_NAME</string>"
        fi
        rm -rf "$(dirname "$iconset")"
    fi

    cat > "$app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>$SHORTCUT_NAME</string>
    <key>CFBundleDisplayName</key>
    <string>$SHORTCUT_NAME</string>
    <key>CFBundleIdentifier</key>
    <string>local.rosarium.launcher</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>$SHORTCUT_NAME</string>
$icon_key
</dict>
</plist>
EOF
    touch "$app"   # nudges the Finder to refresh the icon
    echo "  app created: $app"
    [ -z "$icon_key" ] && echo "  no launch/rosarium.png (or sips/iconutil missing): default app icon used"
    return 0
}

case "$(uname -s)" in
    Darwin) make_app_bundle ;;
    *)      make_desktop_entry ;;
esac
