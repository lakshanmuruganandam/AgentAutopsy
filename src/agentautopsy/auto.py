"""
Drop-in auto-patcher for AgentAutopsy.

Usage:
    import agentautopsy.auto

This automatically calls watch() to start intercepting LLM and HTTP calls
without needing to modify any other code in the application.
"""

from agentautopsy import watch

# Automatically start watching when this module is imported.
watch(agent_name="auto_patched_agent")
