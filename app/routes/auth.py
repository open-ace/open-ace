#!/usr/bin/env python3
"""
Open ACE - Auth Routes

API routes for authentication operations.
"""

import bcrypt

from flask import Blueprint, jsonify, make_response, request

from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService

auth_bp = Blueprint('auth', __name__)
auth_service = AuthService()
user_repo = UserRepository()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception:
        return False


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


@auth_bp.route('/auth/login', methods=['POST'])
def api_login():
    """Login endpoint."""
    data = request.get_json() or {}
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400

    user, token_or_error = auth_service.login(username, password, verify_password)

    if user:
        response = make_response(jsonify({
            'success': True,
            'user': user
        }))
        response.set_cookie(
            'session_token',
            token_or_error,
            httponly=True,
            secure=False,  # Set to True in production with HTTPS
            samesite='Lax',
            max_age=24 * 60 * 60  # 24 hours
        )
        return response

    return jsonify({'error': token_or_error}), 401


@auth_bp.route('/auth/logout', methods=['POST'])
def api_logout():
    """Logout endpoint."""
    token = request.cookies.get('session_token') or request.headers.get('Authorization', '').replace('Bearer ', '')

    if token:
        auth_service.logout(token)

    response = make_response(jsonify({'success': True}))
    response.delete_cookie('session_token')
    return response


@auth_bp.route('/auth/profile', methods=['GET'])
def api_profile():
    """Get current user profile."""
    token = request.cookies.get('session_token') or request.headers.get('Authorization', '').replace('Bearer ', '')

    is_auth, session_or_error = auth_service.require_auth(token)
    if not is_auth:
        return jsonify(session_or_error), 401

    user_id = session_or_error.get('user_id')
    profile = auth_service.get_user_profile(user_id)

    if profile:
        return jsonify(profile)

    return jsonify({'error': 'User not found'}), 404


@auth_bp.route('/auth/check', methods=['GET'])
def api_auth_check():
    """Check if user is authenticated."""
    token = request.cookies.get('session_token') or request.headers.get('Authorization', '').replace('Bearer ', '')

    if not token:
        return jsonify({'authenticated': False})

    session = auth_service.get_session(token)
    if not session:
        return jsonify({'authenticated': False})

    return jsonify({
        'authenticated': True,
        'user': {
            'id': session.get('user_id'),
            'username': session.get('username'),
            'email': session.get('email'),
            'role': session.get('role')
        }
    })


@auth_bp.route('/auth/me', methods=['GET'])
def api_current_user():
    """Get current user info (alias for /auth/profile)."""
    token = request.cookies.get('session_token') or request.headers.get('Authorization', '').replace('Bearer ', '')

    is_auth, session_or_error = auth_service.require_auth(token)
    if not is_auth:
        return jsonify(session_or_error), 401

    user_id = session_or_error.get('user_id')
    profile = auth_service.get_user_profile(user_id)

    if profile:
        return jsonify({'user': profile})

    return jsonify({'error': 'User not found'}), 404
