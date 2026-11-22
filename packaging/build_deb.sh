#!/usr/bin/env bash
# Build an installable .deb with an applications-menu launcher.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="1.1.0"
BUILD="$ROOT/packaging/deb"
OUT="$ROOT/dist"

rm -rf "$BUILD"
mkdir -p "$BUILD/DEBIAN" \
         "$BUILD/usr/bin" \
         "$BUILD/usr/share/applications" \
         "$BUILD/usr/share/icons/hicolor/scalable/apps" \
         "$BUILD/usr/share/instagram-follow-manager" \
         "$OUT"

cat > "$BUILD/DEBIAN/control" <<EOF
Package: instagram-follow-manager
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-tk, python3-selenium
Recommends: google-chrome-stable | chromium-browser
Maintainer: vahidtakro <vahidtakro@users.noreply.github.com>
Homepage: https://github.com/vahidtakro/instagram-follow-manager
Description: GUI tool to clean up Instagram follows
 Cancel outgoing follow requests and unfollow non-followers from a desktop
 application. Includes pause/continue/stop, dry-run safety, resumable history
 with CSV export, and a command-line interface.
EOF

cp -r "$ROOT/insta_manager" "$BUILD/usr/share/instagram-follow-manager/"
cp "$ROOT/app.py" "$ROOT/cli.py" "$BUILD/usr/share/instagram-follow-manager/"
find "$BUILD/usr/share/instagram-follow-manager" -name '__pycache__' -type d -prune -exec rm -rf {} +

cat > "$BUILD/usr/bin/instagram-follow-manager" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/share/instagram-follow-manager/app.py "$@"
EOF
cat > "$BUILD/usr/bin/instagram-follow-manager-cli" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/share/instagram-follow-manager/cli.py "$@"
EOF
chmod 755 "$BUILD/usr/bin/instagram-follow-manager" \
          "$BUILD/usr/bin/instagram-follow-manager-cli"

cat > "$BUILD/usr/share/applications/instagram-follow-manager.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Instagram Follow Manager
GenericName=Instagram Follow Manager
Comment=Cancel follow requests and unfollow non-followers
Exec=instagram-follow-manager
Icon=instagram-follow-manager
Terminal=false
Categories=Network;SocialNetworking;Utility;
Keywords=instagram;follow;unfollow;requests;
StartupNotify=true
EOF

cat > "$BUILD/usr/share/icons/hicolor/scalable/apps/instagram-follow-manager.svg" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
  <defs>
    <linearGradient id="g" x1="0" y1="1" x2="1" y2="0">
      <stop offset="0" stop-color="#f09433"/>
      <stop offset="0.5" stop-color="#dc2743"/>
      <stop offset="1" stop-color="#6a1b9a"/>
    </linearGradient>
  </defs>
  <rect x="8" y="8" width="112" height="112" rx="28" fill="url(#g)"/>
  <rect x="27" y="27" width="74" height="74" rx="22" fill="none" stroke="#fff" stroke-width="8"/>
  <circle cx="64" cy="64" r="19" fill="none" stroke="#fff" stroke-width="8"/>
  <circle cx="87" cy="41" r="6" fill="#fff"/>
  <path d="M47 64 l11 11 23 -23" fill="none" stroke="#fff" stroke-width="8"
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>
EOF

dpkg-deb --build --root-owner-group "$BUILD" "$OUT/instagram-follow-manager_${VERSION}_all.deb"
echo "Built: $OUT/instagram-follow-manager_${VERSION}_all.deb"
