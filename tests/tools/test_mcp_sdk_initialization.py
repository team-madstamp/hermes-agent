from __future__ import annotations

import types


_MISSING = object()


def test_required_sdk_import_failure_does_not_report_available_after_partial_binding(monkeypatch):
    from tools import mcp_tool

    state_names = (
        "ClientSession",
        "StdioServerParameters",
        "stdio_client",
        "streamablehttp_client",
        "streamable_http_client",
        "sse_client",
    )
    state_flags = (
        "_MCP_AVAILABLE",
        "_MCP_HTTP_AVAILABLE",
        "_MCP_NEW_HTTP",
        "_MCP_LEGACY_HTTP",
        "_MCP_SAMPLING_TYPES",
        "_MCP_NOTIFICATION_TYPES",
        "_MCP_ELICITATION_TYPES",
        "_MCP_MESSAGE_HANDLER_SUPPORTED",
        "_MCP_LOGGING_CALLBACK_SUPPORTED",
        "_MCP_SDK_IMPORT_ATTEMPTED",
    )
    saved = {
        name: mcp_tool.__dict__.get(name, _MISSING)
        for name in (*state_names, *state_flags)
    }
    partial_mcp = types.SimpleNamespace(
        ClientSession=object,
        StdioServerParameters=object,
    )
    real_import = mcp_tool.importlib.import_module

    def import_partial_sdk(name):
        if name == "mcp":
            return partial_mcp
        if name == "mcp.client.stdio":
            raise ImportError("stdio transport is unavailable")
        return real_import(name)

    try:
        for name in state_names:
            mcp_tool.__dict__[name] = object()
        for name in state_flags:
            mcp_tool.__dict__[name] = False
        mcp_tool._MCP_AVAILABLE = True
        mcp_tool._MCP_SDK_IMPORT_ATTEMPTED = False
        monkeypatch.setattr(mcp_tool.importlib, "import_module", import_partial_sdk)

        assert mcp_tool._ensure_mcp_sdk() is False
        assert mcp_tool._MCP_AVAILABLE is False
        assert mcp_tool._MCP_SDK_IMPORT_ATTEMPTED is True
        assert mcp_tool.ClientSession is None
        assert mcp_tool.StdioServerParameters is None
        assert mcp_tool.stdio_client is None
        assert mcp_tool.streamable_http_client is None
        assert mcp_tool.streamablehttp_client is None
        assert mcp_tool.sse_client is None
        assert not any(
            getattr(mcp_tool, name)
            for name in (
                "_MCP_HTTP_AVAILABLE",
                "_MCP_NEW_HTTP",
                "_MCP_LEGACY_HTTP",
                "_MCP_SAMPLING_TYPES",
                "_MCP_NOTIFICATION_TYPES",
                "_MCP_ELICITATION_TYPES",
                "_MCP_MESSAGE_HANDLER_SUPPORTED",
                "_MCP_LOGGING_CALLBACK_SUPPORTED",
            )
        )
    finally:
        for name, value in saved.items():
            if value is _MISSING:
                mcp_tool.__dict__.pop(name, None)
            else:
                mcp_tool.__dict__[name] = value
