#!/usr/bin/env python3
"""
Open ACE - AI Computing Explorer - Workspace API Routes

API endpoints for workspace functionality including:
- Prompt templates
- Session management
- Tool connections
- State synchronization
- Collaboration features
"""

import logging

from flask import Blueprint, g, jsonify, request

from app.modules.workspace.collaboration import CollaborationManager, SharePermission
from app.modules.workspace.prompt_library import PromptCategory, PromptLibrary, PromptTemplate
from app.modules.workspace.session_manager import SessionManager, SessionType
from app.modules.workspace.state_sync import get_state_sync_manager
from app.modules.workspace.tool_connector import get_tool_connector

logger = logging.getLogger(__name__)

workspace_bp = Blueprint('workspace', __name__)


# ==================== Prompt Templates ====================

@workspace_bp.route('/prompts', methods=['GET'])
def list_prompts():
    """List prompt templates."""
    try:
        library = PromptLibrary()

        category = request.args.get('category')
        search = request.args.get('search')
        tags = request.args.getlist('tags')
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))

        user_id = g.user.get('id') if hasattr(g, 'user') and g.user else None

        result = library.list_templates(
            category=category,
            user_id=user_id,
            search=search,
            tags=tags if tags else None,
            page=page,
            limit=limit
        )

        return jsonify({
            'success': True,
            'data': {
                'templates': [t.to_dict() for t in result['templates']],
                'total': result['total'],
                'page': result['page'],
                'limit': result['limit'],
                'total_pages': result['total_pages']
            }
        })
    except Exception as e:
        logger.error(f"Error listing prompts: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/prompts', methods=['POST'])
def create_prompt():
    """Create a new prompt template."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400

        user_id = g.user.get('id') if hasattr(g, 'user') and g.user else None
        username = g.user.get('username', '') if hasattr(g, 'user') and g.user else ''

        template = PromptTemplate(
            name=data.get('name', ''),
            description=data.get('description', ''),
            category=data.get('category', PromptCategory.GENERAL.value),
            content=data.get('content', ''),
            variables=data.get('variables', []),
            tags=data.get('tags', []),
            author_id=user_id,
            author_name=username,
            is_public=data.get('is_public', False),
        )

        library = PromptLibrary()
        template_id = library.create_template(template)

        return jsonify({
            'success': True,
            'data': {'id': template_id}
        }), 201
    except Exception as e:
        logger.error(f"Error creating prompt: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/prompts/<int:template_id>', methods=['GET'])
def get_prompt(template_id):
    """Get a prompt template."""
    try:
        library = PromptLibrary()
        template = library.get_template(template_id)

        if not template:
            return jsonify({'success': False, 'error': 'Template not found'}), 404

        return jsonify({
            'success': True,
            'data': template.to_dict()
        })
    except Exception as e:
        logger.error(f"Error getting prompt: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/prompts/<int:template_id>', methods=['PUT'])
def update_prompt(template_id):
    """Update a prompt template."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400

        library = PromptLibrary()
        template = library.get_template(template_id)

        if not template:
            return jsonify({'success': False, 'error': 'Template not found'}), 404

        # Update fields
        template.name = data.get('name', template.name)
        template.description = data.get('description', template.description)
        template.category = data.get('category', template.category)
        template.content = data.get('content', template.content)
        template.variables = data.get('variables', template.variables)
        template.tags = data.get('tags', template.tags)
        template.is_public = data.get('is_public', template.is_public)

        success = library.update_template(template)

        return jsonify({'success': success})
    except Exception as e:
        logger.error(f"Error updating prompt: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/prompts/<int:template_id>', methods=['DELETE'])
def delete_prompt(template_id):
    """Delete a prompt template."""
    try:
        user_id = g.user.get('id') if hasattr(g, 'user') and g.user else None

        library = PromptLibrary()
        success = library.delete_template(template_id, user_id)

        if not success:
            return jsonify({'success': False, 'error': 'Template not found or not authorized'}), 404

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error deleting prompt: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/prompts/<int:template_id>/render', methods=['POST'])
def render_prompt(template_id):
    """Render a prompt template with variables."""
    try:
        data = request.get_json() or {}
        variables = data.get('variables', {})

        library = PromptLibrary()
        template = library.get_template(template_id)

        if not template:
            return jsonify({'success': False, 'error': 'Template not found'}), 404

        # Validate required variables
        missing = template.validate_variables(**variables)
        if missing:
            return jsonify({
                'success': False,
                'error': f'Missing required variables: {", ".join(missing)}'
            }), 400

        # Render the template
        rendered = template.render(**variables)

        # Increment use count
        library.increment_use_count(template_id)

        return jsonify({
            'success': True,
            'data': {'rendered': rendered}
        })
    except Exception as e:
        logger.error(f"Error rendering prompt: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/prompts/categories', methods=['GET'])
def get_prompt_categories():
    """Get prompt categories with counts."""
    try:
        library = PromptLibrary()
        categories = library.get_categories()

        return jsonify({
            'success': True,
            'data': categories
        })
    except Exception as e:
        logger.error(f"Error getting categories: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/prompts/featured', methods=['GET'])
def get_featured_prompts():
    """Get featured prompt templates."""
    try:
        limit = int(request.args.get('limit', 10))
        library = PromptLibrary()
        templates = library.get_featured_templates(limit)

        return jsonify({
            'success': True,
            'data': [t.to_dict() for t in templates]
        })
    except Exception as e:
        logger.error(f"Error getting featured prompts: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Sessions ====================

@workspace_bp.route('/sessions', methods=['GET'])
def list_sessions():
    """List agent sessions."""
    try:
        manager = SessionManager()

        user_id = g.user.get('id') if hasattr(g, 'user') and g.user else None
        tool_name = request.args.get('tool_name')
        status = request.args.get('status')
        session_type = request.args.get('session_type')
        search = request.args.get('search')
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))

        result = manager.list_sessions(
            user_id=user_id,
            tool_name=tool_name,
            status=status,
            session_type=session_type,
            search=search,
            page=page,
            limit=limit
        )

        return jsonify({
            'success': True,
            'data': {
                'sessions': [s.to_dict() for s in result['sessions']],
                'total': result['total'],
                'page': result['page'],
                'limit': result['limit'],
                'total_pages': result['total_pages']
            }
        })
    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/sessions', methods=['POST'])
def create_session():
    """Create a new agent session."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400

        tool_name = data.get('tool_name')
        if not tool_name:
            return jsonify({'success': False, 'error': 'tool_name is required'}), 400

        user_id = g.user.get('id') if hasattr(g, 'user') and g.user else None

        manager = SessionManager()
        session = manager.create_session(
            tool_name=tool_name,
            user_id=user_id,
            session_type=data.get('session_type', SessionType.CHAT.value),
            title=data.get('title', ''),
            host_name=data.get('host_name', 'localhost'),
            context=data.get('context'),
            settings=data.get('settings'),
            model=data.get('model'),
            expires_in_hours=data.get('expires_in_hours')
        )

        return jsonify({
            'success': True,
            'data': session.to_dict()
        }), 201
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/sessions/<session_id>', methods=['GET'])
def get_session(session_id):
    """Get a session by ID."""
    try:
        include_messages = request.args.get('include_messages', 'false').lower() == 'true'

        manager = SessionManager()
        session = manager.get_session(session_id, include_messages=include_messages)

        if not session:
            return jsonify({'success': False, 'error': 'Session not found'}), 404

        return jsonify({
            'success': True,
            'data': session.to_dict()
        })
    except Exception as e:
        logger.error(f"Error getting session: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/sessions/<session_id>/messages', methods=['POST'])
def add_session_message(session_id):
    """Add a message to a session."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400

        role = data.get('role')
        content = data.get('content')

        if not role or not content:
            return jsonify({'success': False, 'error': 'role and content are required'}), 400

        manager = SessionManager()
        message_id = manager.add_message(
            session_id=session_id,
            role=role,
            content=content,
            tokens_used=data.get('tokens_used', 0),
            model=data.get('model'),
            metadata=data.get('metadata')
        )

        return jsonify({
            'success': True,
            'data': {'message_id': message_id}
        }), 201
    except Exception as e:
        logger.error(f"Error adding message: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/sessions/<session_id>/complete', methods=['POST'])
def complete_session(session_id):
    """Mark a session as completed."""
    try:
        manager = SessionManager()
        success = manager.complete_session(session_id)

        if not success:
            return jsonify({'success': False, 'error': 'Session not found'}), 404

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error completing session: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/sessions/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    """Delete a session."""
    try:
        manager = SessionManager()
        success = manager.delete_session(session_id)

        if not success:
            return jsonify({'success': False, 'error': 'Session not found'}), 404

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error deleting session: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/sessions/stats', methods=['GET'])
def get_session_stats():
    """Get session statistics."""
    try:
        user_id = g.user.get('id') if hasattr(g, 'user') and g.user else None

        manager = SessionManager()
        stats = manager.get_session_stats(user_id)

        return jsonify({
            'success': True,
            'data': stats
        })
    except Exception as e:
        logger.error(f"Error getting session stats: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Tools ====================

@workspace_bp.route('/tools', methods=['GET'])
def list_tools():
    """List available AI tools."""
    try:
        connector = get_tool_connector()
        tool_type = request.args.get('type')
        status = request.args.get('status')

        tools = connector.list_tools(tool_type=tool_type, status=status)

        return jsonify({
            'success': True,
            'data': [t.to_dict() for t in tools]
        })
    except Exception as e:
        logger.error(f"Error listing tools: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/tools/<tool_name>', methods=['GET'])
def get_tool(tool_name):
    """Get tool information."""
    try:
        connector = get_tool_connector()
        tool = connector.get_tool(tool_name)

        if not tool:
            return jsonify({'success': False, 'error': 'Tool not found'}), 404

        return jsonify({
            'success': True,
            'data': tool.to_dict()
        })
    except Exception as e:
        logger.error(f"Error getting tool: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/tools/<tool_name>/models', methods=['GET'])
def get_tool_models(tool_name):
    """Get available models for a tool."""
    try:
        connector = get_tool_connector()
        models = connector.get_available_models(tool_name)

        return jsonify({
            'success': True,
            'data': models
        })
    except Exception as e:
        logger.error(f"Error getting tool models: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/tools/health', methods=['GET'])
def check_tools_health():
    """Check health of all tools."""
    try:
        connector = get_tool_connector()
        # For sync context, return cached status
        stats = connector.get_tool_stats()

        return jsonify({
            'success': True,
            'data': stats
        })
    except Exception as e:
        logger.error(f"Error checking tools health: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== State Sync ====================

@workspace_bp.route('/sync/events', methods=['GET'])
def get_sync_events():
    """Get sync events."""
    try:
        manager = get_state_sync_manager()

        event_type = request.args.get('event_type')
        session_id = request.args.get('session_id')
        limit = int(request.args.get('limit', 100))

        user_id = g.user.get('id') if hasattr(g, 'user') and g.user else None

        events = manager.get_events(
            event_type=event_type,
            session_id=session_id,
            user_id=user_id,
            limit=limit
        )

        return jsonify({
            'success': True,
            'data': [e.to_dict() for e in events]
        })
    except Exception as e:
        logger.error(f"Error getting sync events: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/sync/stats', methods=['GET'])
def get_sync_stats():
    """Get sync statistics."""
    try:
        manager = get_state_sync_manager()
        stats = manager.get_stats()

        return jsonify({
            'success': True,
            'data': stats
        })
    except Exception as e:
        logger.error(f"Error getting sync stats: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Collaboration ====================

@workspace_bp.route('/teams', methods=['GET'])
def list_teams():
    """List user's teams."""
    try:
        user_id = g.user.get('id') if hasattr(g, 'user') and g.user else None
        if not user_id:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401

        manager = CollaborationManager()
        teams = manager.list_user_teams(user_id)

        return jsonify({
            'success': True,
            'data': [t.to_dict() for t in teams]
        })
    except Exception as e:
        logger.error(f"Error listing teams: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/teams', methods=['POST'])
def create_team():
    """Create a new team."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400

        user_id = g.user.get('id') if hasattr(g, 'user') and g.user else None
        if not user_id:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401

        name = data.get('name')
        if not name:
            return jsonify({'success': False, 'error': 'name is required'}), 400

        manager = CollaborationManager()
        team = manager.create_team(
            name=name,
            owner_id=user_id,
            description=data.get('description', ''),
            settings=data.get('settings')
        )

        return jsonify({
            'success': True,
            'data': team.to_dict()
        }), 201
    except Exception as e:
        logger.error(f"Error creating team: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/shares', methods=['GET'])
def list_shares():
    """List sessions shared with user."""
    try:
        user_id = g.user.get('id') if hasattr(g, 'user') and g.user else None
        if not user_id:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401

        manager = CollaborationManager()
        shares = manager.get_user_shared_sessions(user_id)

        return jsonify({
            'success': True,
            'data': [s.to_dict() for s in shares]
        })
    except Exception as e:
        logger.error(f"Error listing shares: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/shares', methods=['POST'])
def create_share():
    """Share a session."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400

        user_id = g.user.get('id') if hasattr(g, 'user') and g.user else None
        username = g.user.get('username', '') if hasattr(g, 'user') and g.user else ''

        if not user_id:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401

        session_id = data.get('session_id')
        if not session_id:
            return jsonify({'success': False, 'error': 'session_id is required'}), 400

        manager = CollaborationManager()
        share = manager.share_session(
            session_id=session_id,
            shared_by=user_id,
            shared_by_name=username,
            permission=data.get('permission', SharePermission.VIEW.value),
            share_type=data.get('share_type', 'user'),
            target_id=data.get('target_id'),
            target_name=data.get('target_name', ''),
            expires_in_hours=data.get('expires_in_hours'),
            allow_comments=data.get('allow_comments', True),
            allow_copy=data.get('allow_copy', True)
        )

        return jsonify({
            'success': True,
            'data': share.to_dict()
        }), 201
    except Exception as e:
        logger.error(f"Error creating share: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/shares/<share_id>', methods=['DELETE'])
def revoke_share(share_id):
    """Revoke a share."""
    try:
        user_id = g.user.get('id') if hasattr(g, 'user') and g.user else None

        manager = CollaborationManager()
        success = manager.revoke_share(share_id, user_id)

        if not success:
            return jsonify({'success': False, 'error': 'Share not found or not authorized'}), 404

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error revoking share: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/annotations', methods=['GET'])
def get_annotations():
    """Get annotations for a session."""
    try:
        session_id = request.args.get('session_id')
        if not session_id:
            return jsonify({'success': False, 'error': 'session_id is required'}), 400

        manager = CollaborationManager()
        annotations = manager.get_session_annotations(session_id)

        return jsonify({
            'success': True,
            'data': [a.to_dict() for a in annotations]
        })
    except Exception as e:
        logger.error(f"Error getting annotations: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/annotations', methods=['POST'])
def create_annotation():
    """Create an annotation."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400

        user_id = g.user.get('id') if hasattr(g, 'user') and g.user else None
        username = g.user.get('username', '') if hasattr(g, 'user') and g.user else ''

        if not user_id:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401

        session_id = data.get('session_id')
        content = data.get('content')

        if not session_id or not content:
            return jsonify({'success': False, 'error': 'session_id and content are required'}), 400

        manager = CollaborationManager()
        annotation = manager.add_annotation(
            session_id=session_id,
            user_id=user_id,
            username=username,
            content=content,
            message_id=data.get('message_id'),
            annotation_type=data.get('annotation_type', 'comment'),
            position=data.get('position'),
            parent_id=data.get('parent_id')
        )

        return jsonify({
            'success': True,
            'data': annotation.to_dict()
        }), 201
    except Exception as e:
        logger.error(f"Error creating annotation: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Knowledge Base ====================

@workspace_bp.route('/knowledge', methods=['GET'])
def list_knowledge():
    """List knowledge base entries."""
    try:
        manager = CollaborationManager()

        team_id = request.args.get('team_id')
        category = request.args.get('category')
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))

        result = manager.list_knowledge_entries(
            team_id=team_id,
            category=category,
            page=page,
            limit=limit
        )

        return jsonify({
            'success': True,
            'data': {
                'entries': [e.to_dict() for e in result['entries']],
                'total': result['total'],
                'page': result['page'],
                'limit': result['limit'],
                'total_pages': result['total_pages']
            }
        })
    except Exception as e:
        logger.error(f"Error listing knowledge: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/knowledge', methods=['POST'])
def create_knowledge():
    """Create a knowledge base entry."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400

        user_id = g.user.get('id') if hasattr(g, 'user') and g.user else None
        username = g.user.get('username', '') if hasattr(g, 'user') and g.user else ''

        if not user_id:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401

        title = data.get('title')
        content = data.get('content')

        if not title or not content:
            return jsonify({'success': False, 'error': 'title and content are required'}), 400

        manager = CollaborationManager()
        entry = manager.create_knowledge_entry(
            title=title,
            content=content,
            author_id=user_id,
            author_name=username,
            team_id=data.get('team_id'),
            category=data.get('category', 'general'),
            tags=data.get('tags'),
            is_published=data.get('is_published', False)
        )

        return jsonify({
            'success': True,
            'data': entry.to_dict()
        }), 201
    except Exception as e:
        logger.error(f"Error creating knowledge: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@workspace_bp.route('/knowledge/<entry_id>', methods=['GET'])
def get_knowledge(entry_id):
    """Get a knowledge base entry."""
    try:
        manager = CollaborationManager()
        entry = manager.get_knowledge_entry(entry_id)

        if not entry:
            return jsonify({'success': False, 'error': 'Entry not found'}), 404

        return jsonify({
            'success': True,
            'data': entry.to_dict()
        })
    except Exception as e:
        logger.error(f"Error getting knowledge: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Workspace Config ====================

@workspace_bp.route('/config', methods=['GET'])
def get_workspace_config():
    """Get workspace configuration."""
    import json
    import os

    try:
        from app.repositories.database import CONFIG_DIR
        config_path = os.path.join(CONFIG_DIR, 'config.json')

        workspace_config = {
            'enabled': False,
            'url': ''
        }

        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config = json.load(f)
                workspace = config.get('workspace', {})
                workspace_config['enabled'] = workspace.get('enabled', False)
                workspace_config['url'] = workspace.get('url', '')

        return jsonify(workspace_config)
    except Exception as e:
        logger.error(f"Error getting workspace config: {e}")
        return jsonify({'enabled': False, 'url': ''})


# ==================== Workspace Status ====================

@workspace_bp.route('/status', methods=['GET'])
def get_workspace_status():
    """Get workspace status including model, token usage, and latency."""
    from datetime import datetime, timedelta
    from app.repositories.message_repo import MessageRepository

    try:
        user_id = g.user.get('id') if hasattr(g, 'user') and g.user else None

        # Get today's token usage
        message_repo = MessageRepository()
        today = datetime.now().strftime('%Y-%m-%d')

        # Get user's token usage for today
        user_tokens = message_repo.get_user_token_totals(
            start_date=today,
            end_date=today,
            host_name=None
        )

        # Calculate total tokens used today
        tokens_used = sum(u.get('total_tokens', 0) for u in user_tokens) if user_tokens else 0

        # Get default token limit (could be from config or user quota)
        # For now, use a default of 100,000 tokens per day
        tokens_limit = 100000

        # Get last request time
        last_request = None
        if user_tokens:
            # Get the most recent message timestamp
            last_request = datetime.now().isoformat()

        # Default model (could be from config)
        model = 'GPT-4'

        # Calculate average latency from recent requests
        # For now, return a placeholder
        latency = 0

        status = {
            'model': model,
            'tokens_used': tokens_used,
            'tokens_limit': tokens_limit,
            'latency': latency,
            'last_request': last_request,
            'status': 'active'
        }

        return jsonify(status)
    except Exception as e:
        logger.error(f"Error getting workspace status: {e}")
        return jsonify({
            'model': 'GPT-4',
            'tokens_used': 0,
            'tokens_limit': 100000,
            'latency': 0,
            'last_request': None,
            'status': 'error'
        })
