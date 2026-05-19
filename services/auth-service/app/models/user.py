"""
User and authentication-related data models using SQLAlchemy 2.0.
Async-compatible ORM models with proper relationships and constraints.
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import Column, String, DateTime, Boolean, Integer, Index, Text, Enum
from sqlalchemy.orm import declarative_base
from sqlalchemy.dialects.postgresql import UUID
import uuid
import enum

Base = declarative_base()


class User(Base):
    """User account model with authentication and tracking fields."""
    
    __tablename__ = "users"
    
    # Primary Key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    
    # Authentication
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    
    # Email Verification
    email_verified = Column(Boolean, default=False, nullable=False)
    email_verified_at = Column(DateTime(timezone=True), nullable=True)
    
    # Account Status
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    is_locked = Column(Boolean, default=False, nullable=False)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    
    # Login Tracking
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    login_attempts = Column(Integer, default=0, nullable=False)
    last_login_attempt_at = Column(DateTime(timezone=True), nullable=True)
    
    # MFA
    mfa_enabled = Column(Boolean, default=False, nullable=False)
    mfa_method = Column(String(50), nullable=True)  # 'totp', 'sms', etc.
    
    # Audit
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    
    # Indexes
    __table_args__ = (
        Index("ix_users_email_active", "email", "is_active"),
        Index("ix_users_created_at_desc", "created_at"),
    )
    
    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email}>"


class RefreshToken(Base):
    """Refresh token storage for token rotation."""
    
    __tablename__ = "refresh_tokens"
    
    # Primary Key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    
    # Foreign Key
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    
    # Token Data
    token_hash = Column(String(255), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    
    # Device Tracking
    device_id = Column(String(255), nullable=True)
    user_agent = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)  # IPv6 compatible
    
    # Audit
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index("ix_refresh_tokens_user_id_expires", "user_id", "expires_at"),
    )
    
    def __repr__(self) -> str:
        return f"<RefreshToken id={self.id} user_id={self.user_id}>"


class TokenBlacklist(Base):
    """Blacklist for revoked access tokens."""
    
    __tablename__ = "token_blacklist"
    
    # Primary Key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    
    # Token Data
    token_hash = Column(String(255), nullable=False, unique=True, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    
    # Audit
    revoked_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    reason = Column(String(255), nullable=True)  # logout, password_change, etc.
    
    __table_args__ = (
        Index("ix_token_blacklist_expires_at", "expires_at"),
    )
    
    def __repr__(self) -> str:
        return f"<TokenBlacklist id={self.id} user_id={self.user_id}>"


class LoginAudit(Base):
    """Audit log for login attempts (successful and failed)."""
    
    __tablename__ = "login_audit"
    
    # Primary Key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    
    # User Data
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)  # Nullable for failed login with wrong email
    email = Column(String(255), nullable=False, index=True)
    
    # Status
    status = Column(String(50), nullable=False, index=True)  # 'success', 'failed_password', 'user_not_found', 'account_locked'
    reason = Column(String(255), nullable=True)
    
    # Context
    user_agent = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    device_id = Column(String(255), nullable=True)
    
    # Audit
    timestamp = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True)
    
    __table_args__ = (
        Index("ix_login_audit_user_id_timestamp", "user_id", "timestamp"),
        Index("ix_login_audit_email_timestamp", "email", "timestamp"),
    )
    
    def __repr__(self) -> str:
        return f"<LoginAudit id={self.id} email={self.email} status={self.status}>"
