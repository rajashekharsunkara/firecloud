"""Security hardening for FireCloud.

This module implements:
1. Request signing with Ed25519
2. Rate limiting per node
3. Replay attack protection with nonces
4. Anti-Sybil device fingerprint validation
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError as BadSignature


# ============================================================================
# Request Signing
# ============================================================================

@dataclass
class SignedRequest:
    """A cryptographically signed request."""
    request_id: str
    method: str
    path: str
    body_hash: str  # BLAKE3 hash of body
    timestamp: str  # ISO timestamp
    nonce: str  # Random nonce for replay protection
    device_id: str
    public_key: str
    signature: str
    
    def canonical_message(self) -> bytes:
        """Generate canonical message for signing/verification."""
        parts = [
            self.request_id,
            self.method.upper(),
            self.path,
            self.body_hash,
            self.timestamp,
            self.nonce,
            self.device_id,
        ]
        return "|".join(parts).encode()
    
    def to_headers(self) -> dict[str, str]:
        """Convert to HTTP headers."""
        return {
            "X-FireCloud-Request-ID": self.request_id,
            "X-FireCloud-Timestamp": self.timestamp,
            "X-FireCloud-Nonce": self.nonce,
            "X-FireCloud-Device-ID": self.device_id,
            "X-FireCloud-Public-Key": self.public_key,
            "X-FireCloud-Signature": self.signature,
            "X-FireCloud-Body-Hash": self.body_hash,
        }
    
    @classmethod
    def from_headers(
        cls,
        method: str,
        path: str,
        headers: dict[str, str],
    ) -> "SignedRequest":
        """Parse from HTTP headers."""
        normalized_headers = {key.lower(): value for key, value in headers.items()}
        return cls(
            request_id=normalized_headers.get("x-firecloud-request-id", ""),
            method=method,
            path=path,
            body_hash=normalized_headers.get("x-firecloud-body-hash", ""),
            timestamp=normalized_headers.get("x-firecloud-timestamp", ""),
            nonce=normalized_headers.get("x-firecloud-nonce", ""),
            device_id=normalized_headers.get("x-firecloud-device-id", ""),
            public_key=normalized_headers.get("x-firecloud-public-key", ""),
            signature=normalized_headers.get("x-firecloud-signature", ""),
        )


def sign_request(
    method: str,
    path: str,
    body: bytes,
    device_id: str,
    public_key: str,
    sign_callback: Callable[[bytes], bytes],
) -> SignedRequest:
    """Create a signed request.
    
    Args:
        method: HTTP method (GET, POST, etc.)
        path: Request path
        body: Request body bytes
        device_id: Sender's device ID
        public_key: Sender's public key (hex)
        sign_callback: Function to sign message bytes
    """
    request_id = f"req-{secrets.token_hex(8)}"
    timestamp = datetime.now(timezone.utc).isoformat()
    nonce = secrets.token_hex(16)
    body_hash = hashlib.blake2b(body, digest_size=32).hexdigest()
    
    req = SignedRequest(
        request_id=request_id,
        method=method.upper(),
        path=path,
        body_hash=body_hash,
        timestamp=timestamp,
        nonce=nonce,
        device_id=device_id,
        public_key=public_key,
        signature="",  # Will be set below
    )
    
    message = req.canonical_message()
    signature = sign_callback(message)
    req.signature = signature.hex()
    
    return req


def verify_request_signature(req: SignedRequest) -> bool:
    """Verify a request's signature.
    
    Returns True if signature is valid.
    """
    try:
        verify_key = VerifyKey(bytes.fromhex(req.public_key))
        message = req.canonical_message()
        signature = bytes.fromhex(req.signature)
        verify_key.verify(message, signature)
        return True
    except (BadSignature, ValueError, Exception):
        return False


# ============================================================================
# Rate Limiting
# ============================================================================

@dataclass
class RateLimitConfig:
    """Rate limit configuration."""
    requests_per_minute: int = 60
    requests_per_hour: int = 1000
    requests_per_day: int = 10000
    burst_limit: int = 20  # Max requests in 1 second
    
    # Per-operation limits
    uploads_per_hour: int = 100
    downloads_per_hour: int = 500
    api_calls_per_minute: int = 100


@dataclass
class RateLimitBucket:
    """Token bucket for rate limiting."""
    tokens: float
    last_update: float
    max_tokens: int
    refill_rate: float  # Tokens per second
    
    def consume(self, count: int = 1) -> bool:
        """Try to consume tokens. Returns True if allowed."""
        now = time.monotonic()
        elapsed = now - self.last_update
        
        # Refill tokens
        self.tokens = min(
            self.max_tokens,
            self.tokens + elapsed * self.refill_rate
        )
        self.last_update = now
        
        # Check if we have enough
        if self.tokens >= count:
            self.tokens -= count
            return True
        return False


class RateLimiter:
    """Per-device rate limiter."""
    
    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self.config = config or RateLimitConfig()
        self._lock = RLock()
        
        # Per-device buckets: device_id -> bucket_name -> RateLimitBucket
        self._buckets: dict[str, dict[str, RateLimitBucket]] = defaultdict(dict)
        
        # Blocked devices
        self._blocked: set[str] = set()
        self._block_until: dict[str, float] = {}
        
        # Stats
        self._stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    
    def _get_bucket(
        self,
        device_id: str,
        bucket_name: str,
        max_tokens: int,
        refill_per_second: float,
    ) -> RateLimitBucket:
        """Get or create a rate limit bucket."""
        with self._lock:
            if bucket_name not in self._buckets[device_id]:
                self._buckets[device_id][bucket_name] = RateLimitBucket(
                    tokens=max_tokens,
                    last_update=time.monotonic(),
                    max_tokens=max_tokens,
                    refill_rate=refill_per_second,
                )
            return self._buckets[device_id][bucket_name]
    
    def is_blocked(self, device_id: str) -> bool:
        """Check if device is temporarily blocked."""
        with self._lock:
            if device_id not in self._blocked:
                return False
            
            if time.monotonic() > self._block_until.get(device_id, 0):
                self._blocked.discard(device_id)
                return False
            
            return True
    
    def block_device(self, device_id: str, duration_seconds: int = 300) -> None:
        """Block a device for a duration."""
        with self._lock:
            self._blocked.add(device_id)
            self._block_until[device_id] = time.monotonic() + duration_seconds
    
    def check_rate_limit(
        self,
        device_id: str,
        operation: str = "request",
    ) -> tuple[bool, str]:
        """Check if request is allowed.
        
        Returns:
            Tuple of (allowed, reason)
        """
        if self.is_blocked(device_id):
            return False, "Device temporarily blocked"
        
        with self._lock:
            # Check burst limit (requests in last second)
            burst_bucket = self._get_bucket(
                device_id, "burst",
                max_tokens=self.config.burst_limit,
                refill_per_second=self.config.burst_limit,
            )
            if not burst_bucket.consume():
                self._stats[device_id]["burst_exceeded"] += 1
                return False, "Burst limit exceeded"
            
            # Check per-minute limit
            minute_bucket = self._get_bucket(
                device_id, "minute",
                max_tokens=self.config.requests_per_minute,
                refill_per_second=self.config.requests_per_minute / 60,
            )
            if not minute_bucket.consume():
                self._stats[device_id]["minute_exceeded"] += 1
                return False, "Per-minute limit exceeded"
            
            # Check per-hour limit
            hour_bucket = self._get_bucket(
                device_id, "hour",
                max_tokens=self.config.requests_per_hour,
                refill_per_second=self.config.requests_per_hour / 3600,
            )
            if not hour_bucket.consume():
                self._stats[device_id]["hour_exceeded"] += 1
                return False, "Per-hour limit exceeded"
            
            # Check operation-specific limits
            if operation == "upload":
                upload_bucket = self._get_bucket(
                    device_id, "upload_hour",
                    max_tokens=self.config.uploads_per_hour,
                    refill_per_second=self.config.uploads_per_hour / 3600,
                )
                if not upload_bucket.consume():
                    return False, "Upload limit exceeded"
            
            elif operation == "download":
                download_bucket = self._get_bucket(
                    device_id, "download_hour",
                    max_tokens=self.config.downloads_per_hour,
                    refill_per_second=self.config.downloads_per_hour / 3600,
                )
                if not download_bucket.consume():
                    return False, "Download limit exceeded"
            
            self._stats[device_id]["allowed"] += 1
            return True, "OK"
    
    def get_stats(self, device_id: str) -> dict[str, int]:
        """Get rate limit stats for a device."""
        with self._lock:
            return dict(self._stats.get(device_id, {}))
    
    def reset_device(self, device_id: str) -> None:
        """Reset all rate limits for a device."""
        with self._lock:
            self._buckets.pop(device_id, None)
            self._stats.pop(device_id, None)
            self._blocked.discard(device_id)
            self._block_until.pop(device_id, None)


# ============================================================================
# Replay Protection
# ============================================================================

class NonceStore:
    """Stores used nonces to prevent replay attacks."""
    
    def __init__(
        self,
        max_age_seconds: int = 300,  # 5 minutes
        cleanup_interval: int = 60,  # Cleanup every minute
        db_path: Path | None = None,
    ) -> None:
        self.max_age = max_age_seconds
        self.cleanup_interval = cleanup_interval
        self.db_path = db_path
        self._lock = RLock()

        # nonce -> monotonic timestamp (in-memory mode only)
        self._nonces: dict[str, float] = {}
        self._conn: sqlite3.Connection | None = None
        if self.db_path is not None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            with self._conn:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS security_nonces (
                        nonce TEXT PRIMARY KEY,
                        seen_at REAL NOT NULL
                    )
                    """
                )

        self._last_cleanup = time.time() if self._conn is not None else time.monotonic()
    
    def _cleanup(self) -> None:
        """Remove expired nonces."""
        if self._conn is not None:
            now = time.time()
            if now - self._last_cleanup < self.cleanup_interval:
                return
            cutoff = now - self.max_age
            with self._conn:
                self._conn.execute("DELETE FROM security_nonces WHERE seen_at < ?", (cutoff,))
            self._last_cleanup = now
            return

        now = time.monotonic()
        if now - self._last_cleanup < self.cleanup_interval:
            return

        cutoff = now - self.max_age
        expired = [n for n, ts in self._nonces.items() if ts < cutoff]
        for n in expired:
            del self._nonces[n]

        self._last_cleanup = now
    
    def check_and_store(self, nonce: str) -> bool:
        """Check if nonce is new and store it.
        
        Returns:
            True if nonce is new (allowed), False if replay
        """
        with self._lock:
            self._cleanup()

            if self._conn is not None:
                try:
                    with self._conn:
                        self._conn.execute(
                            "INSERT INTO security_nonces(nonce, seen_at) VALUES(?, ?)",
                            (nonce, time.time()),
                        )
                    return True
                except sqlite3.IntegrityError:
                    return False

            if nonce in self._nonces:
                return False  # Replay!

            self._nonces[nonce] = time.monotonic()
            return True
    
    def is_replay(self, nonce: str) -> bool:
        """Check if nonce has been seen before."""
        with self._lock:
            self._cleanup()
            if self._conn is not None:
                row = self._conn.execute(
                    "SELECT 1 FROM security_nonces WHERE nonce = ? LIMIT 1",
                    (nonce,),
                ).fetchone()
                return row is not None
            return nonce in self._nonces


class TimestampValidator:
    """Validates request timestamps to prevent replay attacks."""
    
    def __init__(
        self,
        max_age_seconds: int = 300,  # 5 minutes
        max_future_seconds: int = 60,  # 1 minute into future (clock skew)
    ) -> None:
        self.max_age = max_age_seconds
        self.max_future = max_future_seconds
    
    def validate(self, timestamp_iso: str) -> tuple[bool, str]:
        """Validate a timestamp.
        
        Returns:
            Tuple of (valid, reason)
        """
        try:
            ts = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            
            age = (now - ts).total_seconds()
            
            if age > self.max_age:
                return False, f"Request too old ({age:.0f}s > {self.max_age}s)"
            
            if age < -self.max_future:
                return False, f"Request timestamp in future ({-age:.0f}s)"
            
            return True, "OK"
            
        except Exception as e:
            return False, f"Invalid timestamp: {e}"


# ============================================================================
# Anti-Sybil Protection
# ============================================================================

@dataclass
class DeviceFingerprint:
    """Device fingerprint for Sybil detection."""
    device_id: str
    public_key: str
    fingerprint_hash: str
    platform: str
    first_seen: str
    last_seen: str
    reputation: float = 100.0
    is_verified: bool = False
    is_banned: bool = False
    ban_reason: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "public_key": self.public_key,
            "fingerprint_hash": self.fingerprint_hash,
            "platform": self.platform,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "reputation": self.reputation,
            "is_verified": self.is_verified,
            "is_banned": self.is_banned,
            "ban_reason": self.ban_reason,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeviceFingerprint":
        return cls(
            device_id=data["device_id"],
            public_key=data["public_key"],
            fingerprint_hash=data["fingerprint_hash"],
            platform=data.get("platform", "unknown"),
            first_seen=data["first_seen"],
            last_seen=data["last_seen"],
            reputation=data.get("reputation", 100.0),
            is_verified=data.get("is_verified", False),
            is_banned=data.get("is_banned", False),
            ban_reason=data.get("ban_reason", ""),
        )


class SybilProtection:
    """Anti-Sybil protection using device fingerprints."""
    
    FINGERPRINTS_FILE = "device_fingerprints.json"
    
    # Similarity thresholds
    FINGERPRINT_SIMILARITY_THRESHOLD = 0.8  # 80% similar = likely same device
    MAX_DEVICES_PER_FINGERPRINT = 1  # Only 1 node per physical device
    
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        
        self._fingerprints: dict[str, DeviceFingerprint] = {}
        self._load_data()
    
    def _load_data(self) -> None:
        """Load fingerprint data."""
        path = self.data_dir / self.FINGERPRINTS_FILE
        if path.exists():
            with open(path, "r") as f:
                data = json.load(f)
                self._fingerprints = {
                    k: DeviceFingerprint.from_dict(v)
                    for k, v in data.items()
                }
    
    def _save_data(self) -> None:
        """Save fingerprint data."""
        with open(self.data_dir / self.FINGERPRINTS_FILE, "w") as f:
            json.dump(
                {k: v.to_dict() for k, v in self._fingerprints.items()},
                f, indent=2
            )
    
    def _calculate_similarity(self, fp1: str, fp2: str) -> float:
        """Calculate similarity between two fingerprint hashes.
        
        Uses hamming distance on the hash bytes.
        """
        if len(fp1) != len(fp2):
            return 0.0
        
        bytes1 = bytes.fromhex(fp1)
        bytes2 = bytes.fromhex(fp2)
        
        # Count matching bytes
        matches = sum(b1 == b2 for b1, b2 in zip(bytes1, bytes2))
        return matches / len(bytes1)
    
    def register_device(
        self,
        device_id: str,
        public_key: str,
        fingerprint_hash: str,
        platform: str,
    ) -> tuple[bool, str]:
        """Register a new device.
        
        Returns:
            Tuple of (allowed, reason)
        """
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            
            # Check if device already registered
            if device_id in self._fingerprints:
                existing = self._fingerprints[device_id]
                
                # Verify fingerprint matches
                if existing.fingerprint_hash != fingerprint_hash:
                    return False, "Device fingerprint changed (possible clone)"
                
                if existing.is_banned:
                    return False, f"Device banned: {existing.ban_reason}"
                
                # Update last seen
                existing.last_seen = now
                self._save_data()
                return True, "Device recognized"
            
            # Check for similar fingerprints (Sybil detection)
            similar_devices = []
            for did, fp in self._fingerprints.items():
                if fp.is_banned:
                    continue
                
                similarity = self._calculate_similarity(
                    fingerprint_hash, fp.fingerprint_hash
                )
                
                if similarity >= self.FINGERPRINT_SIMILARITY_THRESHOLD:
                    similar_devices.append((did, similarity))
            
            if len(similar_devices) >= self.MAX_DEVICES_PER_FINGERPRINT:
                return False, f"Device fingerprint too similar to existing device(s): {similar_devices}"
            
            # Register new device
            self._fingerprints[device_id] = DeviceFingerprint(
                device_id=device_id,
                public_key=public_key,
                fingerprint_hash=fingerprint_hash,
                platform=platform,
                first_seen=now,
                last_seen=now,
            )
            
            self._save_data()
            return True, "Device registered"
    
    def verify_device(
        self,
        device_id: str,
        public_key: str,
        fingerprint_hash: str,
    ) -> tuple[bool, str]:
        """Verify a device's identity.
        
        Returns:
            Tuple of (valid, reason)
        """
        with self._lock:
            if device_id not in self._fingerprints:
                return False, "Device not registered"
            
            fp = self._fingerprints[device_id]
            
            if fp.is_banned:
                return False, f"Device banned: {fp.ban_reason}"
            
            if fp.public_key != public_key:
                return False, "Public key mismatch"
            
            if fp.fingerprint_hash != fingerprint_hash:
                return False, "Fingerprint mismatch (possible clone)"
            
            # Update last seen
            fp.last_seen = datetime.now(timezone.utc).isoformat()
            self._save_data()
            
            return True, "Device verified"
    
    def ban_device(self, device_id: str, reason: str) -> bool:
        """Ban a device."""
        with self._lock:
            if device_id not in self._fingerprints:
                return False
            
            fp = self._fingerprints[device_id]
            fp.is_banned = True
            fp.ban_reason = reason
            self._save_data()
            return True
    
    def unban_device(self, device_id: str) -> bool:
        """Unban a device."""
        with self._lock:
            if device_id not in self._fingerprints:
                return False
            
            fp = self._fingerprints[device_id]
            fp.is_banned = False
            fp.ban_reason = ""
            self._save_data()
            return True
    
    def get_device_info(self, device_id: str) -> DeviceFingerprint | None:
        """Get device fingerprint info."""
        return self._fingerprints.get(device_id)
    
    def list_devices(self, include_banned: bool = False) -> list[DeviceFingerprint]:
        """List all registered devices."""
        devices = list(self._fingerprints.values())
        if not include_banned:
            devices = [d for d in devices if not d.is_banned]
        return devices
    
    def update_reputation(
        self,
        device_id: str,
        delta: float,
        min_rep: float = 0.0,
        max_rep: float = 100.0,
    ) -> float | None:
        """Update a device's reputation score.
        
        Args:
            device_id: Device to update
            delta: Change in reputation (positive or negative)
            min_rep: Minimum reputation
            max_rep: Maximum reputation
            
        Returns:
            New reputation score, or None if device not found
        """
        with self._lock:
            if device_id not in self._fingerprints:
                return None
            
            fp = self._fingerprints[device_id]
            fp.reputation = max(min_rep, min(max_rep, fp.reputation + delta))
            self._save_data()
            return fp.reputation


# ============================================================================
# Security Middleware
# ============================================================================

class SecurityMiddleware:
    """Combined security middleware for FireCloud."""
    
    def __init__(
        self,
        data_dir: Path,
        rate_limit_config: RateLimitConfig | None = None,
        require_signatures: bool = True,
        require_registered_devices: bool = False,
    ) -> None:
        self.data_dir = data_dir
        self.require_signatures = require_signatures
        self.require_registered_devices = require_registered_devices
        
        self.rate_limiter = RateLimiter(rate_limit_config)
        self.nonce_store = NonceStore(db_path=self.data_dir / "security_nonces.db")
        self.timestamp_validator = TimestampValidator()
        self.sybil_protection = SybilProtection(data_dir)
    
    def validate_request(
        self,
        req: SignedRequest,
        body: bytes,
        operation: str = "request",
    ) -> tuple[bool, str]:
        """Validate an incoming request.
        
        Checks:
        1. Rate limiting
        2. Timestamp validity
        3. Nonce (replay protection)
        4. Signature
        5. Device verification
        
        Returns:
            Tuple of (valid, reason)
        """
        # 1. Rate limiting
        allowed, reason = self.rate_limiter.check_rate_limit(
            req.device_id, operation
        )
        if not allowed:
            return False, f"Rate limited: {reason}"
        
        # 2. Timestamp validation
        valid, reason = self.timestamp_validator.validate(req.timestamp)
        if not valid:
            return False, f"Invalid timestamp: {reason}"
        
        # 3. Replay protection
        if not self.nonce_store.check_and_store(req.nonce):
            return False, "Replay attack detected (nonce reused)"
        
        # 4. Body hash verification
        expected_hash = hashlib.blake2b(body, digest_size=32).hexdigest()
        if req.body_hash != expected_hash:
            return False, "Body hash mismatch"
        
        # 5. Signature verification
        if self.require_signatures:
            if not verify_request_signature(req):
                return False, "Invalid signature"
        
        # 6. Device verification (if registered)
        fp = self.sybil_protection.get_device_info(req.device_id)
        if fp is None and self.require_registered_devices:
            return False, "Device not registered"

        if fp:
            if fp.is_banned:
                return False, f"Device banned: {fp.ban_reason}"
            
            if fp.public_key != req.public_key:
                return False, "Public key mismatch with registered device"
        
        return True, "OK"
