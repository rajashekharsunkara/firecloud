"""Audit access control with 51% consensus voting.

This module implements privacy-preserving audit log access:
1. Users must submit an appeal with valid reason/proof
2. Network nodes vote on whether to approve access
3. 51% threshold required for approval
4. Access is time-limited and logged
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class AppealReason(str, Enum):
    """Valid reasons for requesting audit access."""
    LEGAL_INVESTIGATION = "legal_investigation"  # Court order / subpoena
    SECURITY_INCIDENT = "security_incident"  # Active breach investigation
    DATA_RECOVERY = "data_recovery"  # User's own data recovery
    COMPLIANCE_AUDIT = "compliance_audit"  # Regulatory requirement
    DISPUTE_RESOLUTION = "dispute_resolution"  # Resolving user dispute
    SYSTEM_MAINTENANCE = "system_maintenance"  # Admin maintenance


@dataclass
class AuditAppeal:
    """An appeal for audit log access."""
    appeal_id: str
    requester_device_id: str
    requester_public_key: str
    reason: AppealReason
    justification: str  # Detailed explanation
    evidence_hash: str  # Hash of supporting evidence (if any)
    evidence_description: str  # What evidence was provided
    scope_start: str  # ISO timestamp - start of requested range
    scope_end: str  # ISO timestamp - end of requested range
    scope_event_types: list[str]  # Event types requested (empty = all)
    created_at: str
    expires_at: str  # Voting deadline
    status: str = "pending"  # pending, approved, rejected, expired
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "appeal_id": self.appeal_id,
            "requester_device_id": self.requester_device_id,
            "requester_public_key": self.requester_public_key,
            "reason": self.reason.value,
            "justification": self.justification,
            "evidence_hash": self.evidence_hash,
            "evidence_description": self.evidence_description,
            "scope_start": self.scope_start,
            "scope_end": self.scope_end,
            "scope_event_types": self.scope_event_types,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "status": self.status,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditAppeal":
        return cls(
            appeal_id=data["appeal_id"],
            requester_device_id=data["requester_device_id"],
            requester_public_key=data["requester_public_key"],
            reason=AppealReason(data["reason"]),
            justification=data["justification"],
            evidence_hash=data["evidence_hash"],
            evidence_description=data["evidence_description"],
            scope_start=data["scope_start"],
            scope_end=data["scope_end"],
            scope_event_types=data.get("scope_event_types", []),
            created_at=data["created_at"],
            expires_at=data["expires_at"],
            status=data.get("status", "pending"),
        )
    
    def canonical_hash(self) -> str:
        """Generate deterministic hash of appeal for signing."""
        canonical = json.dumps({
            "appeal_id": self.appeal_id,
            "requester_device_id": self.requester_device_id,
            "reason": self.reason.value,
            "justification": self.justification,
            "evidence_hash": self.evidence_hash,
            "scope_start": self.scope_start,
            "scope_end": self.scope_end,
            "scope_event_types": sorted(self.scope_event_types),
        }, sort_keys=True)
        return hashlib.blake2b(canonical.encode(), digest_size=32).hexdigest()


@dataclass
class Vote:
    """A node's vote on an appeal."""
    appeal_id: str
    voter_device_id: str
    voter_public_key: str
    vote: bool  # True = approve, False = reject
    reason: str  # Optional reason for vote
    signature: str  # Ed25519 signature of vote
    timestamp: str
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "appeal_id": self.appeal_id,
            "voter_device_id": self.voter_device_id,
            "voter_public_key": self.voter_public_key,
            "vote": self.vote,
            "reason": self.reason,
            "signature": self.signature,
            "timestamp": self.timestamp,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Vote":
        return cls(
            appeal_id=data["appeal_id"],
            voter_device_id=data["voter_device_id"],
            voter_public_key=data["voter_public_key"],
            vote=data["vote"],
            reason=data.get("reason", ""),
            signature=data["signature"],
            timestamp=data["timestamp"],
        )
    
    def message_to_sign(self) -> bytes:
        """Generate the message that should be signed."""
        return f"{self.appeal_id}:{self.voter_device_id}:{self.vote}:{self.timestamp}".encode()


@dataclass
class AccessGrant:
    """A time-limited grant to access audit logs."""
    grant_id: str
    appeal_id: str
    grantee_device_id: str
    grantee_public_key: str
    scope_start: str
    scope_end: str
    scope_event_types: list[str]
    granted_at: str
    expires_at: str  # When access expires
    access_count: int = 0  # How many times accessed
    max_access_count: int = 100  # Maximum access attempts
    is_revoked: bool = False
    revoked_at: str = ""
    revoked_reason: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "appeal_id": self.appeal_id,
            "grantee_device_id": self.grantee_device_id,
            "grantee_public_key": self.grantee_public_key,
            "scope_start": self.scope_start,
            "scope_end": self.scope_end,
            "scope_event_types": self.scope_event_types,
            "granted_at": self.granted_at,
            "expires_at": self.expires_at,
            "access_count": self.access_count,
            "max_access_count": self.max_access_count,
            "is_revoked": self.is_revoked,
            "revoked_at": self.revoked_at,
            "revoked_reason": self.revoked_reason,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AccessGrant":
        return cls(
            grant_id=data["grant_id"],
            appeal_id=data["appeal_id"],
            grantee_device_id=data["grantee_device_id"],
            grantee_public_key=data["grantee_public_key"],
            scope_start=data["scope_start"],
            scope_end=data["scope_end"],
            scope_event_types=data.get("scope_event_types", []),
            granted_at=data["granted_at"],
            expires_at=data["expires_at"],
            access_count=data.get("access_count", 0),
            max_access_count=data.get("max_access_count", 100),
            is_revoked=data.get("is_revoked", False),
            revoked_at=data.get("revoked_at", ""),
            revoked_reason=data.get("revoked_reason", ""),
        )
    
    def is_valid(self) -> bool:
        """Check if grant is still valid."""
        if self.is_revoked:
            return False
        if self.access_count >= self.max_access_count:
            return False
        now = datetime.now(timezone.utc).isoformat()
        if now > self.expires_at:
            return False
        return True


class AuditConsensusManager:
    """Manages the audit access consensus process."""
    
    APPEALS_FILE = "audit_appeals.json"
    VOTES_FILE = "audit_votes.json"
    GRANTS_FILE = "audit_grants.json"
    
    # Configuration
    VOTING_PERIOD_HOURS = 24  # How long voting is open
    APPROVAL_THRESHOLD = 0.51  # 51% required
    ACCESS_DURATION_HOURS = 48  # How long access lasts after approval
    MIN_VOTERS = 3  # Minimum nodes that must vote
    
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self._appeals: dict[str, AuditAppeal] = {}
        self._votes: dict[str, list[Vote]] = {}  # appeal_id -> votes
        self._grants: dict[str, AccessGrant] = {}
        
        self._load_data()
    
    def _load_data(self) -> None:
        """Load persisted data."""
        appeals_path = self.data_dir / self.APPEALS_FILE
        if appeals_path.exists():
            with open(appeals_path, "r") as f:
                data = json.load(f)
                self._appeals = {
                    k: AuditAppeal.from_dict(v) for k, v in data.items()
                }
        
        votes_path = self.data_dir / self.VOTES_FILE
        if votes_path.exists():
            with open(votes_path, "r") as f:
                data = json.load(f)
                self._votes = {
                    k: [Vote.from_dict(v) for v in vlist]
                    for k, vlist in data.items()
                }
        
        grants_path = self.data_dir / self.GRANTS_FILE
        if grants_path.exists():
            with open(grants_path, "r") as f:
                data = json.load(f)
                self._grants = {
                    k: AccessGrant.from_dict(v) for k, v in data.items()
                }
    
    def _save_data(self) -> None:
        """Persist data to disk."""
        with open(self.data_dir / self.APPEALS_FILE, "w") as f:
            json.dump({k: v.to_dict() for k, v in self._appeals.items()}, f, indent=2)
        
        with open(self.data_dir / self.VOTES_FILE, "w") as f:
            json.dump({
                k: [v.to_dict() for v in vlist]
                for k, vlist in self._votes.items()
            }, f, indent=2)
        
        with open(self.data_dir / self.GRANTS_FILE, "w") as f:
            json.dump({k: v.to_dict() for k, v in self._grants.items()}, f, indent=2)
    
    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
    
    def create_appeal(
        self,
        requester_device_id: str,
        requester_public_key: str,
        reason: AppealReason,
        justification: str,
        evidence: bytes | None = None,
        evidence_description: str = "",
        scope_start: str | None = None,
        scope_end: str | None = None,
        scope_event_types: list[str] | None = None,
    ) -> AuditAppeal:
        """Create a new audit access appeal.
        
        Args:
            requester_device_id: Device ID of requester
            requester_public_key: Public key for verification
            reason: Category of reason
            justification: Detailed explanation
            evidence: Optional evidence bytes (hashed, not stored)
            evidence_description: What the evidence is
            scope_start: Start of time range (default: 30 days ago)
            scope_end: End of time range (default: now)
            scope_event_types: Event types to access (default: all)
        """
        now = datetime.now(timezone.utc)
        
        # Generate appeal ID
        appeal_id = f"appeal-{secrets.token_hex(8)}"
        
        # Hash evidence if provided
        evidence_hash = ""
        if evidence:
            evidence_hash = hashlib.blake2b(evidence, digest_size=32).hexdigest()
        
        # Default scope
        if scope_start is None:
            scope_start = (now - timedelta(days=30)).isoformat()
        if scope_end is None:
            scope_end = now.isoformat()
        
        voting_deadline = now + timedelta(hours=self.VOTING_PERIOD_HOURS)
        
        appeal = AuditAppeal(
            appeal_id=appeal_id,
            requester_device_id=requester_device_id,
            requester_public_key=requester_public_key,
            reason=reason,
            justification=justification,
            evidence_hash=evidence_hash,
            evidence_description=evidence_description,
            scope_start=scope_start,
            scope_end=scope_end,
            scope_event_types=scope_event_types or [],
            created_at=now.isoformat(),
            expires_at=voting_deadline.isoformat(),
            status="pending",
        )
        
        self._appeals[appeal_id] = appeal
        self._votes[appeal_id] = []
        self._save_data()
        
        return appeal
    
    def get_appeal(self, appeal_id: str) -> AuditAppeal | None:
        """Get an appeal by ID."""
        return self._appeals.get(appeal_id)
    
    def list_pending_appeals(self) -> list[AuditAppeal]:
        """List all appeals pending votes."""
        now = self._now_iso()
        return [
            a for a in self._appeals.values()
            if a.status == "pending" and a.expires_at > now
        ]
    
    def submit_vote(
        self,
        appeal_id: str,
        voter_device_id: str,
        voter_public_key: str,
        vote: bool,
        reason: str,
        signature: str,
    ) -> Vote:
        """Submit a vote on an appeal.
        
        Args:
            appeal_id: Appeal to vote on
            voter_device_id: Voter's device ID
            voter_public_key: Voter's public key
            vote: True = approve, False = reject
            reason: Optional reason for vote
            signature: Ed25519 signature of vote
        """
        appeal = self._appeals.get(appeal_id)
        if appeal is None:
            raise ValueError(f"Appeal not found: {appeal_id}")
        
        if appeal.status != "pending":
            raise ValueError(f"Appeal is no longer pending: {appeal.status}")
        
        now = self._now_iso()
        if now > appeal.expires_at:
            raise ValueError("Voting period has expired")
        
        # Check if already voted
        existing_votes = self._votes.get(appeal_id, [])
        for v in existing_votes:
            if v.voter_device_id == voter_device_id:
                raise ValueError("Already voted on this appeal")
        
        # Cannot vote on own appeal
        if voter_device_id == appeal.requester_device_id:
            raise ValueError("Cannot vote on your own appeal")
        
        vote_obj = Vote(
            appeal_id=appeal_id,
            voter_device_id=voter_device_id,
            voter_public_key=voter_public_key,
            vote=vote,
            reason=reason,
            signature=signature,
            timestamp=now,
        )
        
        existing_votes.append(vote_obj)
        self._votes[appeal_id] = existing_votes
        self._save_data()
        
        return vote_obj
    
    def get_votes(self, appeal_id: str) -> list[Vote]:
        """Get all votes for an appeal."""
        return self._votes.get(appeal_id, [])
    
    def get_vote_status(
        self,
        appeal_id: str,
        total_eligible_voters: int,
    ) -> dict[str, Any]:
        """Get current voting status for an appeal.
        
        Args:
            appeal_id: Appeal to check
            total_eligible_voters: Total nodes eligible to vote
        """
        appeal = self._appeals.get(appeal_id)
        if appeal is None:
            return {"error": "Appeal not found"}
        
        votes = self._votes.get(appeal_id, [])
        approve_count = sum(1 for v in votes if v.vote)
        reject_count = sum(1 for v in votes if not v.vote)
        total_votes = len(votes)
        
        # Calculate approval percentage
        if total_votes == 0:
            approval_percent = 0.0
        else:
            approval_percent = approve_count / total_votes
        
        # Check if threshold met
        votes_needed = max(self.MIN_VOTERS, int(total_eligible_voters * self.APPROVAL_THRESHOLD) + 1)
        threshold_met = approve_count >= votes_needed
        
        return {
            "appeal_id": appeal_id,
            "status": appeal.status,
            "approve_count": approve_count,
            "reject_count": reject_count,
            "total_votes": total_votes,
            "total_eligible": total_eligible_voters,
            "approval_percent": approval_percent * 100,
            "threshold_percent": self.APPROVAL_THRESHOLD * 100,
            "votes_needed": votes_needed,
            "threshold_met": threshold_met,
            "voting_expires_at": appeal.expires_at,
            "is_expired": self._now_iso() > appeal.expires_at,
        }
    
    def finalize_appeal(
        self,
        appeal_id: str,
        total_eligible_voters: int,
    ) -> AccessGrant | None:
        """Finalize voting and create access grant if approved.
        
        Args:
            appeal_id: Appeal to finalize
            total_eligible_voters: Total nodes that could have voted
            
        Returns:
            AccessGrant if approved, None if rejected
        """
        appeal = self._appeals.get(appeal_id)
        if appeal is None:
            raise ValueError(f"Appeal not found: {appeal_id}")
        
        if appeal.status != "pending":
            raise ValueError(f"Appeal already finalized: {appeal.status}")
        
        status = self.get_vote_status(appeal_id, total_eligible_voters)
        
        # Must have minimum voters
        if status["total_votes"] < self.MIN_VOTERS:
            appeal.status = "rejected"
            self._save_data()
            return None
        
        if status["threshold_met"]:
            appeal.status = "approved"
            
            # Create access grant
            now = datetime.now(timezone.utc)
            grant = AccessGrant(
                grant_id=f"grant-{secrets.token_hex(8)}",
                appeal_id=appeal_id,
                grantee_device_id=appeal.requester_device_id,
                grantee_public_key=appeal.requester_public_key,
                scope_start=appeal.scope_start,
                scope_end=appeal.scope_end,
                scope_event_types=appeal.scope_event_types,
                granted_at=now.isoformat(),
                expires_at=(now + timedelta(hours=self.ACCESS_DURATION_HOURS)).isoformat(),
            )
            
            self._grants[grant.grant_id] = grant
            self._save_data()
            return grant
        else:
            appeal.status = "rejected"
            self._save_data()
            return None
    
    def check_access(
        self,
        device_id: str,
        public_key: str,
        event_time: str,
        event_type: str,
    ) -> AccessGrant | None:
        """Check if a device has access to a specific audit event.
        
        Returns the grant if access is allowed, None otherwise.
        """
        for grant in self._grants.values():
            if not grant.is_valid():
                continue
            
            if grant.grantee_device_id != device_id:
                continue
            
            if grant.grantee_public_key != public_key:
                continue
            
            # Check time scope
            if event_time < grant.scope_start or event_time > grant.scope_end:
                continue
            
            # Check event type scope
            if grant.scope_event_types and event_type not in grant.scope_event_types:
                continue
            
            return grant
        
        return None
    
    def record_access(self, grant_id: str) -> bool:
        """Record that a grant was used to access logs.
        
        Returns False if grant is no longer valid.
        """
        grant = self._grants.get(grant_id)
        if grant is None or not grant.is_valid():
            return False
        
        grant.access_count += 1
        self._save_data()
        return True
    
    def revoke_grant(
        self,
        grant_id: str,
        reason: str,
    ) -> bool:
        """Revoke an access grant."""
        grant = self._grants.get(grant_id)
        if grant is None:
            return False
        
        grant.is_revoked = True
        grant.revoked_at = self._now_iso()
        grant.revoked_reason = reason
        self._save_data()
        return True
    
    def list_active_grants(self) -> list[AccessGrant]:
        """List all currently active access grants."""
        return [g for g in self._grants.values() if g.is_valid()]

    def get_grant(self, grant_id: str) -> AccessGrant | None:
        """Get a specific grant by ID."""
        return self._grants.get(grant_id)

    def get_active_grant_for_requester(
        self,
        requester_device_id: str,
        requester_public_key: str,
    ) -> AccessGrant | None:
        """Get most recent active grant for a requester, if any."""
        active = [
            grant
            for grant in self._grants.values()
            if grant.is_valid()
            and grant.grantee_device_id == requester_device_id
            and grant.grantee_public_key == requester_public_key
        ]
        if not active:
            return None
        # ISO timestamps sort lexicographically for chronological ordering.
        return sorted(active, key=lambda g: g.granted_at, reverse=True)[0]

    def access_status(
        self,
        requester_device_id: str,
        requester_public_key: str,
    ) -> dict[str, Any]:
        """Get current audit access status for a requester."""
        grant = self.get_active_grant_for_requester(
            requester_device_id=requester_device_id,
            requester_public_key=requester_public_key,
        )
        if grant is None:
            return {"has_access": False, "grant": None}
        return {
            "has_access": True,
            "grant": {
                "grant_id": grant.grant_id,
                "appeal_id": grant.appeal_id,
                "granted_at": grant.granted_at,
                "expires_at": grant.expires_at,
                "access_count": grant.access_count,
                "max_access_count": grant.max_access_count,
                "scope_start": grant.scope_start,
                "scope_end": grant.scope_end,
                "scope_event_types": grant.scope_event_types,
            },
        }
    
    def cleanup_expired(self) -> int:
        """Clean up expired appeals and grants.
        
        Returns count of items cleaned up.
        """
        now = self._now_iso()
        count = 0
        
        # Expire pending appeals past deadline
        for appeal in self._appeals.values():
            if appeal.status == "pending" and appeal.expires_at < now:
                appeal.status = "expired"
                count += 1
        
        self._save_data()
        return count


# Helper functions for creating signed votes

def create_signed_vote(
    appeal_id: str,
    voter_device_id: str,
    vote: bool,
    reason: str,
    sign_callback: Callable[[bytes], bytes],
) -> dict[str, Any]:
    """Create a vote with signature.
    
    Args:
        appeal_id: Appeal being voted on
        voter_device_id: Voter's device ID
        vote: True for approve, False for reject
        reason: Reason for vote
        sign_callback: Function to sign message bytes
        
    Returns:
        Dict with vote data ready for submission
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    message = f"{appeal_id}:{voter_device_id}:{vote}:{timestamp}".encode()
    signature = sign_callback(message).hex()
    
    return {
        "appeal_id": appeal_id,
        "voter_device_id": voter_device_id,
        "vote": vote,
        "reason": reason,
        "signature": signature,
        "timestamp": timestamp,
    }


def verify_vote_signature(
    vote: Vote,
    verify_callback: Callable[[bytes, bytes, str], bool],
) -> bool:
    """Verify a vote's signature.
    
    Args:
        vote: Vote to verify
        verify_callback: Function(message, signature, public_key) -> bool
    """
    message = vote.message_to_sign()
    signature = bytes.fromhex(vote.signature)
    return verify_callback(message, signature, vote.voter_public_key)
