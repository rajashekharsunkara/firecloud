"""Node roles and storage quota management.

This module implements:
1. Storage Node vs Consumer Node role selection
2. Storage quota configuration and enforcement
3. Safe data transfer when switching roles
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class NodeRole(str, Enum):
    """Node role in the network."""
    STORAGE = "storage"  # Provides storage to network
    CONSUMER = "consumer"  # Uses storage from network


@dataclass
class StorageQuota:
    """Storage quota configuration for a storage node."""
    total_bytes: int  # Total storage allocated
    used_bytes: int = 0  # Currently used
    reserved_bytes: int = 0  # Reserved/deadlocked (cannot be reclaimed)
    min_free_bytes: int = 0  # Minimum free space to maintain
    
    @property
    def available_bytes(self) -> int:
        """Bytes available for new data (excluding reserved)."""
        return max(0, self.total_bytes - self.used_bytes - self.min_free_bytes)
    
    @property
    def usage_percent(self) -> float:
        """Storage usage percentage."""
        if self.total_bytes == 0:
            return 0.0
        return (self.used_bytes / self.total_bytes) * 100
    
    def can_store(self, bytes_needed: int) -> bool:
        """Check if we have space for new data."""
        return self.available_bytes >= bytes_needed
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "total_bytes": self.total_bytes,
            "used_bytes": self.used_bytes,
            "reserved_bytes": self.reserved_bytes,
            "min_free_bytes": self.min_free_bytes,
            "available_bytes": self.available_bytes,
            "usage_percent": self.usage_percent,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StorageQuota":
        return cls(
            total_bytes=data["total_bytes"],
            used_bytes=data.get("used_bytes", 0),
            reserved_bytes=data.get("reserved_bytes", 0),
            min_free_bytes=data.get("min_free_bytes", 0),
        )


@dataclass
class DataTransferJob:
    """Represents a data transfer job when switching roles."""
    job_id: str
    source_node_id: str
    total_symbols: int
    transferred_symbols: int = 0
    started_at: str = ""
    completed_at: str = ""
    status: str = "pending"  # pending, running, completed, failed
    error_message: str = ""
    
    @property
    def progress_percent(self) -> float:
        if self.total_symbols == 0:
            return 100.0
        return (self.transferred_symbols / self.total_symbols) * 100
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "source_node_id": self.source_node_id,
            "total_symbols": self.total_symbols,
            "transferred_symbols": self.transferred_symbols,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "progress_percent": self.progress_percent,
            "error_message": self.error_message,
        }


@dataclass
class NodeState:
    """Complete state of a node."""
    device_id: str
    role: NodeRole
    quota: StorageQuota | None = None  # Only for storage nodes
    pending_transfer: DataTransferJob | None = None
    is_online: bool = True
    last_seen: str = ""
    reputation_score: float = 100.0  # 0-100 scale
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "device_id": self.device_id,
            "role": self.role.value,
            "is_online": self.is_online,
            "last_seen": self.last_seen,
            "reputation_score": self.reputation_score,
        }
        if self.quota:
            result["quota"] = self.quota.to_dict()
        if self.pending_transfer:
            result["pending_transfer"] = self.pending_transfer.to_dict()
        return result
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NodeState":
        quota = None
        if "quota" in data:
            quota = StorageQuota.from_dict(data["quota"])
        
        transfer = None
        if "pending_transfer" in data:
            t = data["pending_transfer"]
            transfer = DataTransferJob(
                job_id=t["job_id"],
                source_node_id=t["source_node_id"],
                total_symbols=t["total_symbols"],
                transferred_symbols=t.get("transferred_symbols", 0),
                started_at=t.get("started_at", ""),
                completed_at=t.get("completed_at", ""),
                status=t.get("status", "pending"),
                error_message=t.get("error_message", ""),
            )
        
        return cls(
            device_id=data["device_id"],
            role=NodeRole(data["role"]),
            quota=quota,
            pending_transfer=transfer,
            is_online=data.get("is_online", True),
            last_seen=data.get("last_seen", ""),
            reputation_score=data.get("reputation_score", 100.0),
        )


class NodeRoleManager:
    """Manages node role, quota, and role switching."""
    
    STATE_FILE = "node_state.json"
    
    def __init__(self, data_dir: Path, storage_dir: Path) -> None:
        """
        Args:
            data_dir: Directory for node configuration/state
            storage_dir: Directory for actual data storage
        """
        self.data_dir = data_dir
        self.storage_dir = storage_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._state: NodeState | None = None
        self._transfer_callback: Callable[[DataTransferJob], None] | None = None
    
    @property
    def state_path(self) -> Path:
        return self.data_dir / self.STATE_FILE
    
    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
    
    def load_state(self) -> NodeState | None:
        """Load existing node state."""
        if not self.state_path.exists():
            return None
        try:
            with open(self.state_path, "r") as f:
                data = json.load(f)
            self._state = NodeState.from_dict(data)
            return self._state
        except Exception:
            return None
    
    def save_state(self) -> None:
        """Save current node state."""
        if self._state is None:
            return
        with open(self.state_path, "w") as f:
            json.dump(self._state.to_dict(), f, indent=2)
    
    def initialize_node(
        self,
        device_id: str,
        role: NodeRole,
        storage_bytes: int | None = None,
    ) -> NodeState:
        """Initialize a new node with a role.
        
        Args:
            device_id: Unique device identifier
            role: Initial role (STORAGE or CONSUMER)
            storage_bytes: For storage nodes, total storage to provide
        """
        if self._state is not None:
            raise ValueError("Node already initialized. Use switch_role() to change.")
        
        quota = None
        if role == NodeRole.STORAGE:
            if storage_bytes is None or storage_bytes <= 0:
                raise ValueError("Storage nodes must specify storage_bytes > 0")
            quota = StorageQuota(total_bytes=storage_bytes)
            self.storage_dir.mkdir(parents=True, exist_ok=True)
        
        self._state = NodeState(
            device_id=device_id,
            role=role,
            quota=quota,
            last_seen=self._now_iso(),
        )
        
        self.save_state()
        return self._state
    
    def get_state(self) -> NodeState:
        """Get current node state."""
        if self._state is None:
            state = self.load_state()
            if state is None:
                raise ValueError("Node not initialized. Call initialize_node() first.")
            self._state = state
        
        # Update last seen
        self._state.last_seen = self._now_iso()
        return self._state
    
    def update_quota(
        self,
        total_bytes: int | None = None,
        reserved_bytes: int | None = None,
        min_free_bytes: int | None = None,
    ) -> StorageQuota:
        """Update storage quota settings.
        
        Only valid for storage nodes.
        """
        state = self.get_state()
        if state.role != NodeRole.STORAGE:
            raise ValueError("Only storage nodes have quotas")
        
        if state.quota is None:
            raise ValueError("Storage node missing quota")
        
        if total_bytes is not None:
            if total_bytes < state.quota.used_bytes:
                raise ValueError("Cannot reduce quota below used storage")
            state.quota = StorageQuota(
                total_bytes=total_bytes,
                used_bytes=state.quota.used_bytes,
                reserved_bytes=state.quota.reserved_bytes if reserved_bytes is None else reserved_bytes,
                min_free_bytes=state.quota.min_free_bytes if min_free_bytes is None else min_free_bytes,
            )
        
        if reserved_bytes is not None and total_bytes is None:
            state.quota = StorageQuota(
                total_bytes=state.quota.total_bytes,
                used_bytes=state.quota.used_bytes,
                reserved_bytes=reserved_bytes,
                min_free_bytes=state.quota.min_free_bytes if min_free_bytes is None else min_free_bytes,
            )
        
        if min_free_bytes is not None and total_bytes is None and reserved_bytes is None:
            state.quota = StorageQuota(
                total_bytes=state.quota.total_bytes,
                used_bytes=state.quota.used_bytes,
                reserved_bytes=state.quota.reserved_bytes,
                min_free_bytes=min_free_bytes,
            )
        
        self.save_state()
        return state.quota
    
    def record_storage_used(self, bytes_delta: int) -> bool:
        """Record storage usage change.
        
        Args:
            bytes_delta: Positive for added, negative for removed
            
        Returns:
            True if operation allowed, False if would exceed quota
        """
        state = self.get_state()
        if state.role != NodeRole.STORAGE or state.quota is None:
            return True  # Consumer nodes don't track
        
        if bytes_delta > 0 and not state.quota.can_store(bytes_delta):
            return False  # Would exceed quota
        
        new_used = max(0, state.quota.used_bytes + bytes_delta)
        state.quota = StorageQuota(
            total_bytes=state.quota.total_bytes,
            used_bytes=new_used,
            reserved_bytes=state.quota.reserved_bytes,
            min_free_bytes=state.quota.min_free_bytes,
        )
        
        self.save_state()
        return True
    
    def can_switch_role(self) -> tuple[bool, str]:
        """Check if node can switch roles.
        
        Returns:
            Tuple of (can_switch, reason)
        """
        state = self.get_state()
        
        # Check for pending transfer
        if state.pending_transfer and state.pending_transfer.status in ("pending", "running"):
            return False, "Transfer already in progress"
        
        # Storage -> Consumer requires data transfer
        if state.role == NodeRole.STORAGE:
            if state.quota and state.quota.used_bytes > 0:
                return False, "Must transfer data before switching (has stored data)"
        
        return True, "Ready to switch"
    
    def initiate_role_switch(
        self,
        new_role: NodeRole,
        storage_bytes: int | None = None,
    ) -> DataTransferJob | None:
        """Start the role switch process.
        
        If switching from storage to consumer, creates a transfer job.
        
        Args:
            new_role: Target role
            storage_bytes: For switching to storage, the quota
            
        Returns:
            DataTransferJob if transfer needed, None if immediate switch
        """
        state = self.get_state()
        
        if state.role == new_role:
            return None  # No change needed
        
        can_switch, reason = self.can_switch_role()
        if not can_switch:
            raise ValueError(reason)
        
        # Storage -> Consumer: need to transfer data
        if state.role == NodeRole.STORAGE:
            symbol_count = self._count_stored_symbols()
            if symbol_count > 0:
                import uuid
                job = DataTransferJob(
                    job_id=str(uuid.uuid4()),
                    source_node_id=state.device_id,
                    total_symbols=symbol_count,
                    started_at=self._now_iso(),
                    status="pending",
                )
                state.pending_transfer = job
                self.save_state()
                return job
        
        # Consumer -> Storage or empty Storage -> Consumer: immediate switch
        if new_role == NodeRole.STORAGE:
            if storage_bytes is None or storage_bytes <= 0:
                raise ValueError("Must specify storage_bytes for storage role")
            state.quota = StorageQuota(total_bytes=storage_bytes)
            self.storage_dir.mkdir(parents=True, exist_ok=True)
        else:
            state.quota = None
        
        state.role = new_role
        self.save_state()
        return None
    
    def _count_stored_symbols(self) -> int:
        """Count symbols stored locally."""
        if not self.storage_dir.exists():
            return 0
        count = 0
        for root, _, files in os.walk(self.storage_dir):
            count += len(files)
        return count
    
    def execute_transfer(
        self,
        transfer_symbol_callback: Callable[[Path], bool],
    ) -> bool:
        """Execute pending data transfer.
        
        Args:
            transfer_symbol_callback: Function to transfer a single symbol file.
                                      Returns True on success.
        
        Returns:
            True if transfer completed successfully
        """
        state = self.get_state()
        if state.pending_transfer is None:
            return True  # Nothing to transfer
        
        job = state.pending_transfer
        job.status = "running"
        self.save_state()
        
        try:
            # Walk storage directory and transfer each symbol
            for root, _, files in os.walk(self.storage_dir):
                for filename in files:
                    filepath = Path(root) / filename
                    
                    success = transfer_symbol_callback(filepath)
                    if not success:
                        job.status = "failed"
                        job.error_message = f"Failed to transfer: {filepath}"
                        self.save_state()
                        return False
                    
                    job.transferred_symbols += 1
                    self.save_state()
                    
                    if self._transfer_callback:
                        self._transfer_callback(job)
            
            # All transferred - complete the switch
            job.status = "completed"
            job.completed_at = self._now_iso()
            
            # Clear storage
            shutil.rmtree(self.storage_dir, ignore_errors=True)
            
            # Switch role
            state.role = NodeRole.CONSUMER
            state.quota = None
            state.pending_transfer = None
            
            self.save_state()
            return True
            
        except Exception as e:
            job.status = "failed"
            job.error_message = str(e)
            self.save_state()
            return False
    
    def cancel_transfer(self) -> None:
        """Cancel pending transfer."""
        state = self.get_state()
        if state.pending_transfer:
            state.pending_transfer.status = "cancelled"
            state.pending_transfer = None
            self.save_state()
    
    def set_transfer_callback(self, callback: Callable[[DataTransferJob], None]) -> None:
        """Set callback for transfer progress updates."""
        self._transfer_callback = callback


def human_readable_bytes(size_bytes: int) -> str:
    """Convert bytes to human readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"
