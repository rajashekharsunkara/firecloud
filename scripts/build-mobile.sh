#!/bin/bash
# Build FireCloud Mobile App for Android
# Requires: Flutter 3.24+, Java 17 (Android doesn't support Java 25)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
MOBILE_DIR="$PROJECT_ROOT/mobile"

# Check Java version
JAVA_VER=$(java -version 2>&1 | head -1 | cut -d'"' -f2 | cut -d'.' -f1)
if [[ "$JAVA_VER" -gt 21 ]]; then
    echo "ERROR: Android Gradle Plugin requires Java 17 or 21"
    echo "Current Java version: $(java -version 2>&1 | head -1)"
    echo ""
    echo "To fix:"
    echo "  1. Install Java 17: sudo dnf install java-17-openjdk-devel"
    echo "  2. Set JAVA_HOME: export JAVA_HOME=/usr/lib/jvm/java-17-openjdk"
    echo "  3. Run this script again"
    exit 1
fi

# Check Flutter
if ! command -v flutter &> /dev/null; then
    export PATH="$HOME/.flutter/bin:$PATH"
fi

if ! command -v flutter &> /dev/null; then
    echo "ERROR: Flutter not found. Install Flutter or add it to PATH"
    exit 1
fi

cd "$MOBILE_DIR"

echo "=== FireCloud Mobile Build ==="
echo "Project: $MOBILE_DIR"
echo "Flutter: $(flutter --version | head -1)"
echo "Java: $(java -version 2>&1 | head -1)"
echo ""

# Get dependencies
echo "Getting dependencies..."
flutter pub get

# Analyze code
echo "Analyzing code..."
flutter analyze

# Run tests
echo "Running tests..."
flutter test

# Build APK
BUILD_TYPE="${1:-release}"
echo "Building $BUILD_TYPE APK..."
flutter build apk --$BUILD_TYPE

APK_PATH="$MOBILE_DIR/build/app/outputs/flutter-apk/app-$BUILD_TYPE.apk"
if [[ -f "$APK_PATH" ]]; then
    echo ""
    echo "=== BUILD SUCCESSFUL ==="
    echo "APK: $APK_PATH"
    echo "Size: $(du -h "$APK_PATH" | cut -f1)"
    echo ""
    echo "To install on phone:"
    echo "  1. Connect phone via USB with USB debugging enabled"
    echo "  2. Run: adb install $APK_PATH"
    echo "  OR"
    echo "  Transfer APK to phone and install manually"
else
    echo "ERROR: APK not found at $APK_PATH"
    exit 1
fi
