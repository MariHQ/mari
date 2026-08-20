"""Compatibility facade for the layered agent implementation.

New code imports ``mari_server.application``, ``mari_server.infrastructure``, or
``mari_server.api`` directly. This module remains temporarily for third-party
imports while the old flat server layout is retired.
"""

from mari_server.api.agent import AgentChatIn, agent_chat, router, serialize_sse
from mari_server.application.agent import (
    ANSWER_INSTRUCTIONS,
    AgentOutput,
    AgentPorts,
    ToolBinding,
    ToolOutcome,
    planner_instructions,
    stream_agent_turn,
)
from mari_server.domain.navigation import PRODUCT_SURFACES, valid_navigation
from mari_server.infrastructure.agent_tools import (
    UNTRUSTED_CLOSE,
    UNTRUSTED_OPEN,
    ToolDependencies,
    build_tool_bindings,
    safe_document_body,
)

valid_nav = valid_navigation

__all__ = [
    "ANSWER_INSTRUCTIONS", "AgentChatIn", "AgentOutput", "AgentPorts",
    "PRODUCT_SURFACES", "ToolBinding", "ToolDependencies", "ToolOutcome",
    "UNTRUSTED_CLOSE", "UNTRUSTED_OPEN", "agent_chat", "build_tool_bindings",
    "planner_instructions", "router", "safe_document_body", "serialize_sse",
    "stream_agent_turn", "valid_nav", "valid_navigation",
]
