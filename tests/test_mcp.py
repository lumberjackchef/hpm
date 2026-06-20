"""Tests for the MCP server handlers."""

from unittest.mock import patch

import pytest


@pytest.fixture
def mcp_server():
    """Import and return the MCP server module (lazy import for isolation)."""
    from hpm_mcp_server import (
        handle_memory_find,
        handle_memory_save,
        handle_memory_capture,
        TOOL_DEFINITIONS,
    )
    return {
        "find": handle_memory_find,
        "save": handle_memory_save,
        "capture": handle_memory_capture,
        "tools": TOOL_DEFINITIONS,
    }


class TestToolDefinitions:
    def test_tools_are_defined(self, mcp_server):
        """Tool definitions contain the expected tools."""
        names = [t["name"] for t in mcp_server["tools"]]
        assert "memory-find" in names
        assert "memory-save" in names
        assert "memory-capture" in names

    def test_tool_has_descriptions(self, mcp_server):
        """Each tool has a non-empty description."""
        for t in mcp_server["tools"]:
            assert t.get("description")


class TestMemoryFind:
    def test_returns_dont_know_on_empty(self, mcp_server):
        """handle_memory_find returns 'I don't know' when no results found."""
        with patch("hpm_mcp_server.db_module.query_hybrid", return_value=[]):
            result = mcp_server["find"]("test query")
        assert "don't know" in result.lower()


class TestMemorySave:
    def test_returns_id_on_success(self, mcp_server):
        """handle_memory_save returns the saved memory ID."""
        fake_id = "abc-123"
        with patch("hpm_mcp_server.db_module.insert_memory", return_value=fake_id):
            result = mcp_server["save"]("some fact")
        assert fake_id in result


class TestMemoryCapture:
    def test_returns_id_on_success(self, mcp_server):
        """handle_memory_capture returns the saved memory ID."""
        fake_id = "captured-456"
        with patch("hpm_mcp_server.db_module.insert_memory", return_value=fake_id):
            result = mcp_server["capture"]("some turn")
        assert fake_id in result
