#!/usr/bin/env python3
"""
Open ACE - Tool Connector Module

Provides a unified interface for connecting to various AI tools.
Supports tool registration, routing, and health monitoring.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ToolStatus(Enum):
    """Tool status enumeration."""
    ONLINE = 'online'
    OFFLINE = 'offline'
    MAINTENANCE = 'maintenance'
    UNKNOWN = 'unknown'


class ToolType(Enum):
    """Tool type enumeration."""
    CHAT = 'chat'
    AGENT = 'agent'
    WORKFLOW = 'workflow'
    EMBEDDING = 'embedding'
    IMAGE = 'image'


@dataclass
class ToolInfo:
    """Information about an AI tool."""
    name: str
    display_name: str
    tool_type: str = ToolType.CHAT.value
    description: str = ''
    version: str = ''
    endpoint: str = ''
    status: str = ToolStatus.UNKNOWN.value
    capabilities: List[str] = field(default_factory=list)
    models: List[str] = field(default_factory=list)
    default_model: Optional[str] = None
    max_tokens: int = 4096
    supports_streaming: bool = False
    supports_vision: bool = False
    supports_tools: bool = False
    config: Dict[str, Any] = field(default_factory=dict)
    last_check: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'name': self.name,
            'display_name': self.display_name,
            'tool_type': self.tool_type,
            'description': self.description,
            'version': self.version,
            'endpoint': self.endpoint,
            'status': self.status,
            'capabilities': self.capabilities,
            'models': self.models,
            'default_model': self.default_model,
            'max_tokens': self.max_tokens,
            'supports_streaming': self.supports_streaming,
            'supports_vision': self.supports_vision,
            'supports_tools': self.supports_tools,
            'config': self.config,
            'last_check': self.last_check.isoformat() if self.last_check else None,
            'metadata': self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ToolInfo':
        """Create from dictionary."""
        return cls(
            name=data.get('name', ''),
            display_name=data.get('display_name', ''),
            tool_type=data.get('tool_type', ToolType.CHAT.value),
            description=data.get('description', ''),
            version=data.get('version', ''),
            endpoint=data.get('endpoint', ''),
            status=data.get('status', ToolStatus.UNKNOWN.value),
            capabilities=data.get('capabilities', []),
            models=data.get('models', []),
            default_model=data.get('default_model'),
            max_tokens=data.get('max_tokens', 4096),
            supports_streaming=data.get('supports_streaming', False),
            supports_vision=data.get('supports_vision', False),
            supports_tools=data.get('supports_tools', False),
            config=data.get('config', {}),
            last_check=datetime.fromisoformat(data['last_check']) if data.get('last_check') else None,
            metadata=data.get('metadata', {}),
        )


class ToolAdapter(ABC):
    """Abstract base class for tool adapters."""

    @abstractmethod
    async def send_message(
        self,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Send a message to the tool.

        Args:
            message: The message to send.
            session_id: Optional session ID for context.
            model: Optional model to use.
            **kwargs: Additional tool-specific parameters.

        Returns:
            Dict containing the response.
        """
        pass

    @abstractmethod
    async def check_health(self) -> bool:
        """
        Check if the tool is healthy.

        Returns:
            bool: True if healthy, False otherwise.
        """
        pass

    @abstractmethod
    def get_info(self) -> ToolInfo:
        """
        Get tool information.

        Returns:
            ToolInfo: Information about the tool.
        """
        pass


class MockToolAdapter(ToolAdapter):
    """Mock adapter for testing purposes."""

    def __init__(self, tool_info: ToolInfo):
        self.tool_info = tool_info

    async def send_message(
        self,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Send a mock message."""
        return {
            'success': True,
            'response': f"Mock response from {self.tool_info.name}: {message[:50]}...",
            'tokens_used': len(message.split()),
            'model': model or self.tool_info.default_model,
        }

    async def check_health(self) -> bool:
        """Mock health check."""
        return True

    def get_info(self) -> ToolInfo:
        """Get mock tool info."""
        return self.tool_info


class ToolConnector:
    """
    Unified tool connector for managing AI tool connections.

    Provides:
    - Tool registration and discovery
    - Request routing
    - Health monitoring
    - Load balancing (basic)
    """

    def __init__(self):
        """Initialize the tool connector."""
        self._tools: Dict[str, ToolInfo] = {}
        self._adapters: Dict[str, ToolAdapter] = {}
        self._handlers: Dict[str, Callable] = {}
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register default AI tools."""
        default_tools = [
            ToolInfo(
                name='claude',
                display_name='Claude (Anthropic)',
                tool_type=ToolType.CHAT.value,
                description='Anthropic\'s Claude AI assistant',
                models=['claude-3-opus', 'claude-3-sonnet', 'claude-3-haiku'],
                default_model='claude-3-sonnet',
                max_tokens=200000,
                supports_streaming=True,
                supports_vision=True,
                supports_tools=True,
                capabilities=['chat', 'analysis', 'coding', 'writing'],
            ),
            ToolInfo(
                name='qwen',
                display_name='Qwen (Alibaba)',
                tool_type=ToolType.CHAT.value,
                description='Alibaba\'s Qwen AI assistant',
                models=['qwen-turbo', 'qwen-plus', 'qwen-max'],
                default_model='qwen-plus',
                max_tokens=32000,
                supports_streaming=True,
                supports_vision=True,
                supports_tools=True,
                capabilities=['chat', 'analysis', 'coding', 'writing'],
            ),
            ToolInfo(
                name='openclaw',
                display_name='OpenClaw',
                tool_type=ToolType.AGENT.value,
                description='OpenClaw agent platform',
                models=['default'],
                default_model='default',
                max_tokens=128000,
                supports_streaming=True,
                supports_tools=True,
                capabilities=['chat', 'agent', 'workflow', 'tools'],
            ),
            ToolInfo(
                name='openai',
                display_name='OpenAI GPT',
                tool_type=ToolType.CHAT.value,
                description='OpenAI\'s GPT models',
                models=['gpt-4-turbo', 'gpt-4', 'gpt-3.5-turbo'],
                default_model='gpt-4-turbo',
                max_tokens=128000,
                supports_streaming=True,
                supports_vision=True,
                supports_tools=True,
                capabilities=['chat', 'analysis', 'coding', 'writing'],
            ),
        ]

        for tool in default_tools:
            self._tools[tool.name] = tool

        logger.info(f"Registered {len(default_tools)} default tools")

    def register_tool(
        self,
        tool_info: ToolInfo,
        adapter: Optional[ToolAdapter] = None
    ) -> None:
        """
        Register a new tool.

        Args:
            tool_info: Information about the tool.
            adapter: Optional tool adapter for communication.
        """
        self._tools[tool_info.name] = tool_info

        if adapter:
            self._adapters[tool_info.name] = adapter

        logger.info(f"Registered tool: {tool_info.name}")

    def unregister_tool(self, tool_name: str) -> bool:
        """
        Unregister a tool.

        Args:
            tool_name: Name of the tool to unregister.

        Returns:
            bool: True if tool was unregistered.
        """
        if tool_name in self._tools:
            del self._tools[tool_name]
            self._adapters.pop(tool_name, None)
            self._handlers.pop(tool_name, None)
            logger.info(f"Unregistered tool: {tool_name}")
            return True
        return False

    def get_tool(self, tool_name: str) -> Optional[ToolInfo]:
        """
        Get tool information.

        Args:
            tool_name: Name of the tool.

        Returns:
            ToolInfo or None if not found.
        """
        return self._tools.get(tool_name)

    def list_tools(
        self,
        tool_type: Optional[str] = None,
        status: Optional[str] = None
    ) -> List[ToolInfo]:
        """
        List all registered tools.

        Args:
            tool_type: Filter by tool type.
            status: Filter by status.

        Returns:
            List of ToolInfo objects.
        """
        tools = list(self._tools.values())

        if tool_type:
            tools = [t for t in tools if t.tool_type == tool_type]

        if status:
            tools = [t for t in tools if t.status == status]

        return tools

    def set_adapter(self, tool_name: str, adapter: ToolAdapter) -> None:
        """
        Set the adapter for a tool.

        Args:
            tool_name: Name of the tool.
            adapter: Tool adapter instance.
        """
        self._adapters[tool_name] = adapter
        logger.info(f"Set adapter for tool: {tool_name}")

    def set_handler(self, tool_name: str, handler: Callable) -> None:
        """
        Set a custom handler for a tool.

        Args:
            tool_name: Name of the tool.
            handler: Handler function.
        """
        self._handlers[tool_name] = handler
        logger.info(f"Set handler for tool: {tool_name}")

    async def send_message(
        self,
        tool_name: str,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Send a message to a tool.

        Args:
            tool_name: Name of the tool to use.
            message: Message to send.
            session_id: Optional session ID.
            model: Optional model to use.
            **kwargs: Additional parameters.

        Returns:
            Dict containing the response.

        Raises:
            ValueError: If tool not found.
        """
        if tool_name not in self._tools:
            raise ValueError(f"Tool not found: {tool_name}")

        tool = self._tools[tool_name]

        # Check if tool is available
        if tool.status == ToolStatus.OFFLINE.value:
            return {
                'success': False,
                'error': f"Tool {tool_name} is offline",
            }

        if tool.status == ToolStatus.MAINTENANCE.value:
            return {
                'success': False,
                'error': f"Tool {tool_name} is under maintenance",
            }

        # Use custom handler if set
        if tool_name in self._handlers:
            try:
                result = self._handlers[tool_name](
                    message=message,
                    session_id=session_id,
                    model=model,
                    **kwargs
                )
                return result
            except Exception as e:
                logger.error(f"Handler error for {tool_name}: {e}")
                return {
                    'success': False,
                    'error': str(e),
                }

        # Use adapter if set
        if tool_name in self._adapters:
            try:
                result = await self._adapters[tool_name].send_message(
                    message=message,
                    session_id=session_id,
                    model=model,
                    **kwargs
                )
                return result
            except Exception as e:
                logger.error(f"Adapter error for {tool_name}: {e}")
                return {
                    'success': False,
                    'error': str(e),
                }

        # No handler or adapter - return mock response
        return {
            'success': True,
            'response': f"Message sent to {tool_name} (no adapter configured)",
            'message': message[:100],
            'model': model or tool.default_model,
        }

    async def check_tool_health(self, tool_name: str) -> bool:
        """
        Check health of a specific tool.

        Args:
            tool_name: Name of the tool to check.

        Returns:
            bool: True if healthy.
        """
        if tool_name not in self._tools:
            return False

        if tool_name in self._adapters:
            try:
                is_healthy = await self._adapters[tool_name].check_health()
                self._tools[tool_name].status = ToolStatus.ONLINE.value if is_healthy else ToolStatus.OFFLINE.value
                self._tools[tool_name].last_check = datetime.utcnow()
                return is_healthy
            except Exception as e:
                logger.error(f"Health check failed for {tool_name}: {e}")
                self._tools[tool_name].status = ToolStatus.OFFLINE.value
                self._tools[tool_name].last_check = datetime.utcnow()
                return False

        # No adapter - assume online
        self._tools[tool_name].status = ToolStatus.ONLINE.value
        self._tools[tool_name].last_check = datetime.utcnow()
        return True

    async def check_all_health(self) -> Dict[str, bool]:
        """
        Check health of all tools.

        Returns:
            Dict mapping tool names to health status.
        """
        results = {}
        for tool_name in self._tools:
            results[tool_name] = await self.check_tool_health(tool_name)
        return results

    def get_available_models(self, tool_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get available models.

        Args:
            tool_name: Optional tool name to filter by.

        Returns:
            List of model info dictionaries.
        """
        models = []

        tools = [self._tools[tool_name]] if tool_name else list(self._tools.values())

        for tool in tools:
            if tool:
                for model in tool.models:
                    models.append({
                        'tool': tool.name,
                        'model': model,
                        'is_default': model == tool.default_model,
                        'max_tokens': tool.max_tokens,
                    })

        return models

    def get_tool_capabilities(self, tool_name: str) -> List[str]:
        """
        Get capabilities of a tool.

        Args:
            tool_name: Name of the tool.

        Returns:
            List of capability strings.
        """
        tool = self._tools.get(tool_name)
        return tool.capabilities if tool else []

    def find_tools_by_capability(self, capability: str) -> List[ToolInfo]:
        """
        Find tools that have a specific capability.

        Args:
            capability: Capability to search for.

        Returns:
            List of matching ToolInfo objects.
        """
        return [
            tool for tool in self._tools.values()
            if capability in tool.capabilities
        ]

    def update_tool_status(self, tool_name: str, status: str) -> bool:
        """
        Update the status of a tool.

        Args:
            tool_name: Name of the tool.
            status: New status value.

        Returns:
            bool: True if update was successful.
        """
        if tool_name not in self._tools:
            return False

        self._tools[tool_name].status = status
        self._tools[tool_name].last_check = datetime.utcnow()
        logger.info(f"Updated status for {tool_name}: {status}")
        return True

    def get_tool_stats(self) -> Dict[str, Any]:
        """
        Get statistics about registered tools.

        Returns:
            Dict with tool statistics.
        """
        total = len(self._tools)
        online = sum(1 for t in self._tools.values() if t.status == ToolStatus.ONLINE.value)
        offline = sum(1 for t in self._tools.values() if t.status == ToolStatus.OFFLINE.value)
        maintenance = sum(1 for t in self._tools.values() if t.status == ToolStatus.MAINTENANCE.value)

        by_type: Dict[str, int] = {}
        for tool in self._tools.values():
            by_type[tool.tool_type] = by_type.get(tool.tool_type, 0) + 1

        return {
            'total': total,
            'online': online,
            'offline': offline,
            'maintenance': maintenance,
            'by_type': by_type,
            'adapters_configured': len(self._adapters),
            'handlers_configured': len(self._handlers),
        }


# Global tool connector instance
_tool_connector: Optional[ToolConnector] = None


def get_tool_connector() -> ToolConnector:
    """
    Get the global tool connector instance.

    Returns:
        ToolConnector: The global instance.
    """
    global _tool_connector
    if _tool_connector is None:
        _tool_connector = ToolConnector()
    return _tool_connector
