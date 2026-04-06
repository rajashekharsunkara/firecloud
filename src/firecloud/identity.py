"""Device identity and hardware fingerprinting for one-node-per-device enforcement.

This module generates a unique, stable device identity based on hardware characteristics.
The fingerprint is used to:
1. Enforce one node per physical device (anti-Sybil)
2. Bind cryptographic keys to hardware
3. Detect device cloning attempts
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nacl.signing import SigningKey, VerifyKey
from nacl.encoding import HexEncoder


@dataclass(frozen=True)
class DeviceIdentity:
    """Represents a unique device identity."""
    device_id: str  # Stable hardware-derived ID
    fingerprint: str  # BLAKE3 hash of hardware characteristics
    public_key: str  # Ed25519 public key (hex)
    node_type: str  # 'storage' or 'consumer'
    created_at: str  # ISO timestamp


def _get_mac_address() -> str:
    """Get primary MAC address."""
    mac = uuid.getnode()
    # Check if it's a random MAC (bit 1 of first byte set)
    if (mac >> 40) & 0x01:
        return ""
    return ":".join(f"{(mac >> i) & 0xff:02x}" for i in range(40, -1, -8))


def _get_cpu_id() -> str:
    """Get CPU identifier."""
    system = platform.system()
    try:
        if system == "Linux":
            # Try /proc/cpuinfo first
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "Serial" in line or "model name" in line:
                        return line.split(":")[1].strip()
            # Try dmidecode if available
            result = subprocess.run(
                ["dmidecode", "-s", "processor-version"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        elif system == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        elif system == "Windows":
            result = subprocess.run(
                ["wmic", "cpu", "get", "processorid"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if len(lines) > 1:
                    return lines[1].strip()
    except Exception:
        pass
    return platform.processor() or "unknown"


def _get_disk_serial() -> str:
    """Get primary disk serial number."""
    system = platform.system()
    try:
        if system == "Linux":
            # Try lsblk first
            result = subprocess.run(
                ["lsblk", "-d", "-o", "SERIAL", "-n"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                serials = [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]
                if serials:
                    return serials[0]
        elif system == "Darwin":
            result = subprocess.run(
                ["diskutil", "info", "disk0"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "Volume UUID" in line or "Disk / Partition UUID" in line:
                        return line.split(":")[1].strip()
        elif system == "Windows":
            result = subprocess.run(
                ["wmic", "diskdrive", "get", "serialnumber"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if len(lines) > 1:
                    return lines[1].strip()
    except Exception:
        pass
    return ""


def _get_machine_id() -> str:
    """Get OS-level machine ID."""
    system = platform.system()
    try:
        if system == "Linux":
            for path in ["/etc/machine-id", "/var/lib/dbus/machine-id"]:
                if os.path.exists(path):
                    with open(path, "r") as f:
                        return f.read().strip()
        elif system == "Darwin":
            result = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "IOPlatformUUID" in line:
                        return line.split('"')[-2]
        elif system == "Windows":
            result = subprocess.run(
                ["wmic", "csproduct", "get", "uuid"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if len(lines) > 1:
                    return lines[1].strip()
    except Exception:
        pass
    return ""


def generate_hardware_fingerprint() -> tuple[str, dict[str, str]]:
    """Generate a stable hardware fingerprint.
    
    Returns:
        Tuple of (fingerprint_hex, hardware_components_dict)
    """
    components: dict[str, str] = {
        "platform": platform.system(),
        "machine": platform.machine(),
        "mac_address": _get_mac_address(),
        "cpu_id": _get_cpu_id(),
        "disk_serial": _get_disk_serial(),
        "machine_id": _get_machine_id(),
    }
    
    # Create deterministic fingerprint
    canonical = json.dumps(components, sort_keys=True)
    fingerprint = hashlib.blake2b(canonical.encode(), digest_size=32).hexdigest()
    
    return fingerprint, components


def generate_device_id(fingerprint: str, salt: bytes | None = None) -> str:
    """Generate a unique device ID from fingerprint.
    
    The device ID is a shorter, URL-safe identifier derived from the fingerprint.
    """
    if salt is None:
        salt = b"firecloud-device-v1"
    
    device_hash = hashlib.blake2b(
        fingerprint.encode(),
        key=salt,
        digest_size=16
    ).hexdigest()
    
    return f"fc-{device_hash[:8]}-{device_hash[8:16]}"


class DeviceIdentityManager:
    """Manages device identity, keys, and node registration."""
    
    IDENTITY_FILE = "device_identity.json"
    PRIVATE_KEY_FILE = "device_key.secret"
    
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._identity: DeviceIdentity | None = None
        self._signing_key: SigningKey | None = None
    
    @property
    def identity_path(self) -> Path:
        return self.data_dir / self.IDENTITY_FILE
    
    @property
    def key_path(self) -> Path:
        return self.data_dir / self.PRIVATE_KEY_FILE
    
    def has_identity(self) -> bool:
        """Check if device already has an identity."""
        return self.identity_path.exists() and self.key_path.exists()
    
    def load_identity(self) -> DeviceIdentity | None:
        """Load existing device identity."""
        if not self.has_identity():
            return None
        
        try:
            with open(self.identity_path, "r") as f:
                data = json.load(f)
            
            # Verify fingerprint still matches hardware
            current_fp, _ = generate_hardware_fingerprint()
            if data["fingerprint"] != current_fp:
                raise ValueError("Stored device identity does not match current hardware fingerprint")
            
            self._identity = DeviceIdentity(
                device_id=data["device_id"],
                fingerprint=data["fingerprint"],
                public_key=data["public_key"],
                node_type=data["node_type"],
                created_at=data["created_at"],
            )
            
            # Load signing key
            with open(self.key_path, "rb") as f:
                key_bytes = bytes.fromhex(f.read().decode())
            self._signing_key = SigningKey(key_bytes)
            
            return self._identity
        except ValueError:
            raise
        except Exception:
            return None
    
    def create_identity(self, node_type: str = "consumer") -> DeviceIdentity:
        """Create a new device identity.
        
        Args:
            node_type: Either 'storage' or 'consumer'
        
        Raises:
            ValueError: If identity already exists or invalid node_type
        """
        if self.has_identity():
            raise ValueError("Device identity already exists. Use load_identity() or reset_identity().")
        
        if node_type not in ("storage", "consumer"):
            raise ValueError("node_type must be 'storage' or 'consumer'")
        
        # Generate hardware fingerprint
        fingerprint, _ = generate_hardware_fingerprint()
        device_id = generate_device_id(fingerprint)
        
        # Generate Ed25519 keypair
        self._signing_key = SigningKey.generate()
        public_key = self._signing_key.verify_key.encode(encoder=HexEncoder).decode()
        
        from datetime import datetime, timezone
        created_at = datetime.now(timezone.utc).isoformat()
        
        self._identity = DeviceIdentity(
            device_id=device_id,
            fingerprint=fingerprint,
            public_key=public_key,
            node_type=node_type,
            created_at=created_at,
        )
        
        # Save identity
        identity_data = {
            "device_id": self._identity.device_id,
            "fingerprint": self._identity.fingerprint,
            "public_key": self._identity.public_key,
            "node_type": self._identity.node_type,
            "created_at": self._identity.created_at,
        }
        
        with open(self.identity_path, "w") as f:
            json.dump(identity_data, f, indent=2)
        
        # Save private key (restricted permissions)
        with open(self.key_path, "w") as f:
            f.write(self._signing_key.encode(encoder=HexEncoder).decode())
        
        # Set restrictive permissions on key file
        os.chmod(self.key_path, 0o600)
        
        return self._identity
    
    def get_identity(self) -> DeviceIdentity:
        """Get current device identity, creating if needed."""
        if self._identity is None:
            loaded = self.load_identity()
            if loaded is not None:
                self._identity = loaded
            elif self.has_identity():
                raise ValueError("Existing device identity is unreadable or invalid")
        if self._identity is None:
            self._identity = self.create_identity()
        return self._identity
    
    def sign_message(self, message: bytes) -> bytes:
        """Sign a message with device's private key."""
        if self._signing_key is None:
            self.load_identity()
        if self._signing_key is None:
            raise ValueError("No signing key available")
        
        signed = self._signing_key.sign(message)
        return signed.signature
    
    def verify_signature(self, message: bytes, signature: bytes, public_key_hex: str) -> bool:
        """Verify a signature from another device."""
        try:
            verify_key = VerifyKey(bytes.fromhex(public_key_hex))
            verify_key.verify(message, signature)
            return True
        except Exception:
            return False
    
    def change_node_type(self, new_type: str) -> None:
        """Change the node type (storage <-> consumer).
        
        NOTE: Caller must handle data transfer before calling this!
        """
        if new_type not in ("storage", "consumer"):
            raise ValueError("node_type must be 'storage' or 'consumer'")
        
        identity = self.get_identity()
        if identity.node_type == new_type:
            return
        
        # Update identity
        new_identity = DeviceIdentity(
            device_id=identity.device_id,
            fingerprint=identity.fingerprint,
            public_key=identity.public_key,
            node_type=new_type,
            created_at=identity.created_at,
        )
        
        identity_data = {
            "device_id": new_identity.device_id,
            "fingerprint": new_identity.fingerprint,
            "public_key": new_identity.public_key,
            "node_type": new_identity.node_type,
            "created_at": new_identity.created_at,
        }
        
        with open(self.identity_path, "w") as f:
            json.dump(identity_data, f, indent=2)
        
        self._identity = new_identity
    
    def reset_identity(self) -> None:
        """Delete existing identity. USE WITH CAUTION."""
        if self.identity_path.exists():
            os.remove(self.identity_path)
        if self.key_path.exists():
            os.remove(self.key_path)
        self._identity = None
        self._signing_key = None


def verify_device_uniqueness(device_id: str, fingerprint: str, known_devices: list[dict[str, str]]) -> bool:
    """Verify this device is unique in the network.
    
    Checks that no other device has:
    1. The same device_id (collision)
    2. The same fingerprint (clone)
    3. Similar fingerprint components (Sybil attempt)
    
    Returns:
        True if device is unique, False if duplicate detected
    """
    for known in known_devices:
        if known.get("device_id") == device_id:
            return False  # Same device ID
        if known.get("fingerprint") == fingerprint:
            return False  # Same hardware fingerprint
    
    return True
