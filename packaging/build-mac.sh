#!/usr/bin/env bash
# Builds Cue.app and Cue-<version>-mac-arm64.dmg on an Apple Silicon Mac (or the macos-14 runner).
#   bash packaging/build-mac.sh [OUT_DIR]       (default: ./build-mac)
# Needs: python3 -m pip install -r requirements.txt pyinstaller "setuptools<70", and
#        models/wespeaker_en_voxceleb_resnet34_LM.onnx in the repo.
# The app is ad-hoc signed, not notarized: the first open is right-click > Open.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$REPO/build-mac}"
PY="${PYTHON:-python3}"
cd "$REPO"

RELEASE="$($PY -c 'import cue; print(cue.__release__)')"
echo "Cue $RELEASE (macOS arm64) -> $OUT"
mkdir -p "$OUT" packaging/build

# app icon: logo-512.png -> Cue.icns
ICONSET="$OUT/Cue.iconset"
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s assets/logo-512.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  d=$((s * 2)); [ $d -le 512 ] && sips -z $d $d assets/logo-512.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
cp assets/logo-512.png "$ICONSET/icon_512x512@2x.png"
iconutil -c icns "$ICONSET" -o packaging/build/Cue.icns

$PY -m PyInstaller packaging/cue-mac.spec --noconfirm --workpath "$OUT/build" --distpath "$OUT/dist"
APP="$OUT/dist/Cue.app"

# ad-hoc signature over the whole bundle (Apple Silicon refuses to run unsigned code at all)
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

# disk image: Cue.app plus an Applications shortcut to drag it onto
STAGE="$OUT/dmg"
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="$OUT/Cue-$RELEASE-mac-arm64.dmg"
rm -f "$DMG"
hdiutil create -volname "Cue $RELEASE" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
du -h "$DMG"
echo "$DMG"
