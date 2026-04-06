#!/usr/bin/env bash
# FireCloud Desktop Build Script

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DESKTOP_DIR="$ROOT_DIR/desktop"

echo "=== FireCloud Desktop Build ==="

usage() {
    cat <<'EOF'
Usage:
  ./scripts/build-desktop.sh [--auto|--host|--container]

Modes:
  --auto       Use host build if GTK deps exist, otherwise fallback to podman.
  --host       Build on host only (requires GTK/WebKit dev packages installed).
  --container  Build in rootless podman Fedora container (deb/rpm bundles).
EOF
}

check_dep() {
    if pkg-config --exists "$1" 2>/dev/null; then
        echo "✓ $1"
        return 0
    fi
    echo "✗ $1 - MISSING"
    return 1
}

host_deps_ok() {
    local missing=0
    check_dep "webkit2gtk-4.1" || missing=1
    check_dep "libsoup-3.0" || missing=1
    check_dep "javascriptcoregtk-4.1" || missing=1
    return "$missing"
}

build_host() {
    echo
    echo "Building on host..."
    cd "$DESKTOP_DIR"
    npm install
    npm run tauri:build -- --bundles deb,rpm
}

build_container() {
    if ! command -v podman >/dev/null 2>&1; then
        echo "Error: podman is required for --container mode." >&2
        exit 1
    fi
    echo
    echo "Building in rootless Fedora container..."
    podman run --rm \
      -v "$DESKTOP_DIR:/workspace:Z" \
      -w /workspace \
      fedora:43 \
      bash -lc "set -euo pipefail && \
        dnf -y install nodejs npm rust cargo gcc gcc-c++ make pkgconf-pkg-config \
        openssl-devel webkit2gtk4.1-devel libsoup3-devel javascriptcoregtk4.1-devel \
        gtk3-devel libappindicator-gtk3-devel librsvg2-devel xdg-utils && \
        npm install && \
        npm run tauri:build -- --bundles deb,rpm"
}

MODE="${1:---auto}"
case "$MODE" in
  --help|-h)
    usage
    exit 0
    ;;
  --host)
    echo
    echo "Checking host dependencies..."
    if ! host_deps_ok; then
      echo
      echo "Missing host deps. Install with:"
      echo "  sudo dnf install webkit2gtk4.1-devel libsoup3-devel javascriptcoregtk4.1-devel"
      exit 1
    fi
    build_host
    ;;
  --container)
    build_container
    ;;
  --auto)
    echo
    echo "Checking host dependencies..."
    if host_deps_ok; then
      build_host
    else
      echo
      echo "Host deps missing, falling back to container build."
      build_container
    fi
    ;;
  *)
    echo "Unknown option: $MODE" >&2
    usage
    exit 1
    ;;
esac

echo
echo "✅ Build complete!"
echo "Artifacts:"
echo "  $DESKTOP_DIR/src-tauri/target/release/bundle/deb/FireCloud_1.0.0_amd64.deb"
echo "  $DESKTOP_DIR/src-tauri/target/release/bundle/rpm/FireCloud-1.0.0-1.x86_64.rpm"
