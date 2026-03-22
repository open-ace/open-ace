#!/usr/bin/env python3
"""
Open ACE - AI Computing Explorer - Auth Service

Business logic for authentication and authorization.
"""

import logging
import secrets
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

# Session token expiration time (24 hours)
SESSION_EXPIRATION_HOURS = 24


class AuthService:
    """Service for authentication-related business logic."""

    def __init__(self, user_repo: Optional[UserRepository] = None):
        """
        Initialize service.
        
        Args:
            user_repo: Optional UserRepository instance for dependency injection.
        """
        self.user_repo = user_repo or UserRepository()

    def login(
        self,
        username: str,
        password: str,
        password_verify_func
    ) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Authenticate a user and create a session.
        
        Args:
            username: Username.
            password: Plain text password.
            password_verify_func: Function to verify password hash.
            
        Returns:
            Tuple[Optional[Dict], Optional[str]]: (User data, Session token) or (None, error message).
        """
        # Get user by username
        user = self.user_repo.get_user_by_username(username)

        if not user:
            logger.warning(f"Login failed: user not found - {username}")
            return None, "Invalid username or password"

        if not user.get('is_active'):
            logger.warning(f"Login failed: user inactive - {username}")
            return None, "Account is disabled"

        # Verify password
        if not password_verify_func(password, user.get('password_hash', '')):
            logger.warning(f"Login failed: invalid password - {username}")
            return None, "Invalid username or password"

        # Create session token
        token = secrets.token_hex(32)
        expires_at = datetime.utcnow() + timedelta(hours=SESSION_EXPIRATION_HOURS)

        if not self.user_repo.create_session(user['id'], token, expires_at):
            logger.error(f"Failed to create session for user - {username}")
            return None, "Failed to create session"

        # Update last login
        self.user_repo.update_last_login(user['id'])

        logger.info(f"User logged in: {username}")

        return {
            'id': user['id'],
            'username': user['username'],
            'email': user.get('email'),
            'role': user['role']
        }, token

    def logout(self, token: str) -> bool:
        """
        Logout a user by deleting their session.
        
        Args:
            token: Session token.
            
        Returns:
            bool: True if successful.
        """
        return self.user_repo.delete_session(token)

    def get_session(self, token: str) -> Optional[Dict]:
        """
        Get session data by token.
        
        Args:
            token: Session token.
            
        Returns:
            Optional[Dict]: Session data or None.
        """
        return self.user_repo.get_session_by_token(token)

    def get_user_profile(self, user_id: int) -> Optional[Dict]:
        """
        Get user profile.
        
        Args:
            user_id: User ID.
            
        Returns:
            Optional[Dict]: User profile or None.
        """
        user = self.user_repo.get_user_by_id(user_id)
        if user:
            # Remove sensitive data
            user.pop('password_hash', None)
        return user

    def is_admin(self, token: str) -> bool:
        """
        Check if the session belongs to an admin user.
        
        Args:
            token: Session token.
            
        Returns:
            bool: True if admin.
        """
        session = self.get_session(token)
        return session and session.get('role') == 'admin'

    def require_auth(self, token: str) -> Tuple[bool, Optional[Dict]]:
        """
        Require authentication and return session data.
        
        Args:
            token: Session token.
            
        Returns:
            Tuple[bool, Optional[Dict]]: (Is authenticated, Session data or error).
        """
        if not token:
            return False, {'error': 'Authentication required'}

        session = self.get_session(token)
        if not session:
            return False, {'error': 'Invalid or expired session'}

        return True, session

    def require_admin(self, token: str) -> Tuple[bool, Optional[Dict]]:
        """
        Require admin role and return session data.
        
        Args:
            token: Session token.
            
        Returns:
            Tuple[bool, Optional[Dict]]: (Is admin, Session data or error).
        """
        is_auth, session = self.require_auth(token)
        if not is_auth:
            return False, session

        if session.get('role') != 'admin':
            return False, {'error': 'Admin access required'}

        return True, session
