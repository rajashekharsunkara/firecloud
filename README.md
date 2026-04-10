# FireCloud

**Fully Decentralized P2P Distributed Storage System**

Each device IS a node. No central servers. Files are encrypted, chunked, and distributed across peers.

Python Core + Flutter Mobile (Android/iOS) + Tauri Desktop (Win/Mac/Linux) with:

- **Monochrome UI** with smooth animations
- **Google Authentication** for cross-device file access
- **Storage Lock** with garbage fill to reserve space
- **XChaCha20-Poly1305** authenticated encryption
- **RaptorQ 3-of-5** erasure coding (survive node failures)
- **FastCDC** content-defined chunking for deduplication
- **mDNS peer discovery** for P2P networking (no server needed!)
- **Device identity** with hardware fingerprinting
- **Role-based participation**: Storage Provider or Consumer

## Mobile App (Flutter) - v2.0

The mobile app is a **fully standalone P2P node**. Each phone:
- Runs its own HTTP server on port 4001
- Discovers peers via mDNS multicast
- Stores encrypted chunks locally
- Can operate offline and sync when peers are available

### Features
- 📁 **Files**: Upload, download, delete - distributed across network
- 🌐 **Network**: View connected peers, storage providers
- ⚙️ **Settings**: Role selection, storage lock, Google sign-in, theme

### Build Android APK

```bash
cd mobile

# Requires Java 17 (not 22+)
export JAVA_HOME=~/.jdk17  # or your JDK 17 path

# Get dependencies
flutter pub get

# Build release APK
flutter build apk --release
# Output: build/app/outputs/flutter-apk/app-release.apk (~52MB)

# Install on connected device
adb install build/app/outputs/flutter-apk/app-release.apk
```

### Build iOS

```bash
cd mobile

# Requires Xcode 16+ and macOS
flutter build ios --release
# Then archive in Xcode for App Store or .ipa export
```

**Firebase iOS setup required for Google Sign-In:**

1. In Firebase Console, add iOS app with bundle ID: `com.firecloud.app`
2. Download `GoogleService-Info.plist`
3. Place it at: `mobile/ios/Runner/GoogleService-Info.plist`

## Run API backend (required for desktop/mobile clients)

```bash
cd /home/rajashekharsunkara/Documents/firecloud

# Uses FIRECLOUD_ROOT_DIR if set, otherwise ./.firecloud
./scripts/run-api.sh --host 127.0.0.1 --port 8080
```

### Optional bootstrap peers for discovery refresh

You can seed peer discovery from one or more API nodes:

```bash
.venv/bin/firecloud \
  --root-dir .firecloud \
  --bootstrap-peer http://192.168.1.20:8080 \
  --bootstrap-peer http://192.168.1.21:8080 \
  run-api --host 0.0.0.0 --port 8080
```

Bootstrap endpoints:
- `GET /network/bootstrap/status`
- `POST /network/bootstrap/refresh`

### Google Sign-In Setup

1. Create project in [Google Cloud Console](https://console.cloud.google.com)
2. Enable Google Sign-In API
3. Create OAuth credentials for Android/iOS
4. Add `google-services.json` (Android) or `GoogleService-Info.plist` (iOS)

## Desktop App (Tauri) - v1.0

Cross-platform desktop app with monochrome theme.

```bash
cd desktop

# Install dependencies
npm install

# Development
npm run tauri:dev

# Build for current platform
npm run tauri:build
```

If host system dependencies are missing on Linux, use containerized build:

```bash
podman run --rm \
  -v "$(pwd)/desktop:/workspace:Z" \
  -w /workspace \
  fedora:43 \
  bash -lc "dnf -y install nodejs npm rust cargo gcc gcc-c++ make pkgconf-pkg-config openssl-devel webkit2gtk4.1-devel libsoup3-devel javascriptcoregtk4.1-devel gtk3-devel libappindicator-gtk3-devel librsvg2-devel xdg-utils && npm install && npm run tauri:build -- --bundles deb,rpm"
```

Artifacts:
- `desktop/src-tauri/target/release/bundle/deb/FireCloud_1.0.0_amd64.deb`
- `desktop/src-tauri/target/release/bundle/rpm/FireCloud-1.0.0-1.x86_64.rpm`

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        EACH DEVICE                               │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                      FIRECLOUD NODE                        │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │  │
│  │  │ HTTP Server │  │ Peer        │  │ Local Storage       │ │  │
│  │  │ Port 4001   │  │ Discovery   │  │ Encrypted Chunks    │ │  │
│  │  │ API for P2P │  │ mDNS        │  │ File Manifests      │ │  │
│  │  └─────────────┘  └─────────────┘  └─────────────────────┘ │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │  │
│  │  │ Chunking    │  │ Encryption  │  │ Identity            │ │  │
│  │  │ FastCDC     │  │ AES-256     │  │ Device Keys         │ │  │
│  │  └─────────────┘  └─────────────┘  └─────────────────────┘ │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
         ↕ P2P via mDNS (224.0.0.251:5353) ↕
┌──────────────────────────────────────────────────────────────────┐
│                      OTHER DEVICES (PEERS)                       │
└──────────────────────────────────────────────────────────────────┘
```

## How It Works

1. **Upload**: File → FastCDC chunks → Encrypt each chunk → Distribute to peers
2. **Download**: Request chunks from peers → Decrypt → Reassemble file
3. **Discovery**: Devices announce via mDNS every 30 seconds
4. **Roles**: 
   - **Consumer**: Uses network storage (files distributed to providers)
   - **Storage Provider**: Hosts chunks for others (can lock storage)

## Security

- **No Central Server**: All data stays on participant devices
- **End-to-End Encryption**: Chunks encrypted before distribution
- **Device Identity**: Hardware fingerprinting prevents Sybil attacks
- **Storage Lock**: Garbage fill reserves space, released for real data

## Development

```bash
# Python backend (for testing/development)
./scripts/bootstrap.sh
.venv/bin/firecloud --help

# Run tests
./scripts/test.sh
```

## Requirements

| Platform | Requirements |
|----------|--------------|
| Android | Flutter 3.24+, JDK 17, Android SDK 35 |
| iOS | Flutter 3.24+, Xcode 16+, macOS |
| Desktop | Node.js 18+, Rust 1.70+, Tauri CLI 2.0+ |

## Files

- `mobile/` - Flutter app (Android/iOS)
- `desktop/` - Tauri app (Windows/macOS/Linux)
- `src/` - Python core library
- `scripts/` - Build and run scripts
- [TODO.md](TODO.md) - Implementation backlog
- [plan.md](plan.md) - Technical specification
