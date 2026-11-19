"""Tests for tools/execute_script.py — security gating and structured responses."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from fmod_studio_mcp.bridge.tcp_client import FmodTcpClient
from fmod_studio_mcp.tools.execute_script import execute_script


def _make_client(response: str) -> FmodTcpClient:
    """Create a FmodTcpClient mock returning a canned response from eval_js."""
    client = MagicMock(spec=FmodTcpClient)
    client.eval_js = AsyncMock(return_value=response)
    return client


def _js_ok(result) -> str:
    """Simulate FMOD JS wrapper ok response."""
    return json.dumps({"status": "ok", "result": result})


def _js_error(message: str) -> str:
    """Simulate FMOD JS wrapper error response."""
    return json.dumps({"status": "error", "error": message})


class TestExecuteScriptOk:
    """Normal execution path — script runs and returns a result."""

    @pytest.mark.asyncio
    async def test_returns_ok_with_result(self) -> None:
        client = _make_client(_js_ok(42))
        resp = await execute_script(client, "return 42;")
        assert resp["status"] == "ok"
        assert resp["result"] == 42

    @pytest.mark.asyncio
    async def test_returns_ok_with_null_result(self) -> None:
        # Scripts with no return value → null/__result=undefined → null
        client = _make_client(_js_ok(None))
        resp = await execute_script(client, "studio.project.save();")
        assert resp["status"] == "ok"
        assert resp["result"] is None

    @pytest.mark.asyncio
    async def test_returns_ok_with_string_result(self) -> None:
        client = _make_client(_js_ok("hello"))
        resp = await execute_script(client, "return 'hello';")
        assert resp["status"] == "ok"
        assert resp["result"] == "hello"

    @pytest.mark.asyncio
    async def test_returns_ok_with_list_result(self) -> None:
        client = _make_client(_js_ok(["event:/SFX/test"]))
        resp = await execute_script(client, "return ['event:/SFX/test'];")
        assert resp["status"] == "ok"
        assert resp["result"] == ["event:/SFX/test"]

    @pytest.mark.asyncio
    async def test_eval_js_called_with_wrapped_script(self) -> None:
        """eval_js must receive the wrapped script (containing JSON.stringify), not the raw user script."""
        client = _make_client(_js_ok(None))
        await execute_script(client, "return 1;")
        call_args = client.eval_js.call_args
        sent_script = call_args[0][0]  # first positional arg
        assert "JSON.stringify" in sent_script
        assert "return 1;" in sent_script

    @pytest.mark.asyncio
    async def test_timeout_ms_converted_to_seconds(self) -> None:
        """timeout_ms=5000 must be passed to eval_js as timeout_s=5.0."""
        client = _make_client(_js_ok(None))
        await execute_script(client, "return 1;", timeout_ms=5000)
        _, kwargs = client.eval_js.call_args
        assert kwargs.get("timeout_s") == pytest.approx(5.0)

    @pytest.mark.asyncio
    async def test_custom_timeout_ms(self) -> None:
        client = _make_client(_js_ok(None))
        await execute_script(client, "return 1;", timeout_ms=10000)
        _, kwargs = client.eval_js.call_args
        assert kwargs.get("timeout_s") == pytest.approx(10.0)


class TestExecuteScriptBlocked:
    """Security gate — blocked scripts must never reach FMOD Studio."""

    @pytest.mark.asyncio
    async def test_blocked_script_returns_blocked_status(self) -> None:
        client = _make_client("")  # eval_js should NOT be called
        resp = await execute_script(client, "studio.system.start()")
        assert resp["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_blocked_script_has_blocked_code(self) -> None:
        client = _make_client("")
        resp = await execute_script(client, "studio.system.start()")
        assert resp["error"]["code"] == "BLOCKED"

    @pytest.mark.asyncio
    async def test_blocked_script_never_calls_eval_js(self) -> None:
        """FMOD must not receive the blocked script."""
        client = _make_client("")
        await execute_script(client, "triggerAction(6)")
        client.eval_js.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocked_detail_includes_patterns(self) -> None:
        client = _make_client("")
        resp = await execute_script(client, "studio.project.close()")
        detail = resp["error"]["detail"]
        assert "blocked_patterns" in detail
        assert len(detail["blocked_patterns"]) >= 1

    @pytest.mark.asyncio
    async def test_blocked_detail_includes_sent_script(self) -> None:
        client = _make_client("")
        resp = await execute_script(client, "File.open('/etc/passwd')")
        assert "sent_script" in resp["error"]["detail"]

    @pytest.mark.asyncio
    async def test_sent_script_truncated_at_500_chars(self) -> None:
        # Create a script > 500 chars that also triggers a block
        long_suffix = "x" * 600
        script = f"studio.system.stop(); // {long_suffix}"
        client = _make_client("")
        resp = await execute_script(client, script)
        sent = resp["error"]["detail"]["sent_script"]
        assert len(sent) <= 550  # 500 + truncation suffix overhead


class TestExecuteScriptErrors:
    """Error classification — TimeoutError, ConnectionError, parse failures, JS errors."""

    @pytest.mark.asyncio
    async def test_timeout_error_returns_timeout_code(self) -> None:
        client = MagicMock(spec=FmodTcpClient)
        client.eval_js = AsyncMock(side_effect=TimeoutError())
        resp = await execute_script(client, "return 1;")
        assert resp["status"] == "error"
        assert resp["error"]["code"] == "TIMEOUT"

    @pytest.mark.asyncio
    async def test_connection_error_returns_connection_error_code(self) -> None:
        client = MagicMock(spec=FmodTcpClient)
        client.eval_js = AsyncMock(side_effect=ConnectionError("refused"))
        resp = await execute_script(client, "return 1;")
        assert resp["status"] == "error"
        assert resp["error"]["code"] == "CONNECTION_ERROR"

    @pytest.mark.asyncio
    async def test_connection_error_message_included(self) -> None:
        client = MagicMock(spec=FmodTcpClient)
        client.eval_js = AsyncMock(side_effect=ConnectionError("Connection refused"))
        resp = await execute_script(client, "return 1;")
        assert "Connection refused" in resp["error"]["message"]

    @pytest.mark.asyncio
    async def test_invalid_json_response_returns_parse_error(self) -> None:
        client = _make_client("not valid json {{")
        resp = await execute_script(client, "return 1;")
        assert resp["status"] == "error"
        assert resp["error"]["code"] == "PARSE_ERROR"

    @pytest.mark.asyncio
    async def test_syntax_error_classified_correctly(self) -> None:
        client = _make_client(_js_error("SyntaxError: Unexpected token '{'"))
        resp = await execute_script(client, "var x = {{{;")
        assert resp["status"] == "error"
        assert resp["error"]["code"] == "SYNTAX_ERROR"

    @pytest.mark.asyncio
    async def test_runtime_error_classified_correctly(self) -> None:
        client = _make_client(_js_error("TypeError: Cannot read property 'x' of undefined"))
        resp = await execute_script(client, "return undefined.x;")
        assert resp["status"] == "error"
        assert resp["error"]["code"] == "RUNTIME_ERROR"

    @pytest.mark.asyncio
    async def test_generic_js_error_classified_as_runtime_error(self) -> None:
        client = _make_client(_js_error("ReferenceError: foo is not defined"))
        resp = await execute_script(client, "return foo;")
        assert resp["status"] == "error"
        assert resp["error"]["code"] == "RUNTIME_ERROR"

    @pytest.mark.asyncio
    async def test_error_detail_includes_sent_script(self) -> None:
        client = _make_client(_js_error("RuntimeError: something"))
        resp = await execute_script(client, "bad_code()")
        assert "sent_script" in resp["error"]["detail"]

    @pytest.mark.asyncio
    async def test_unexpected_wrapper_status_returns_parse_error(self) -> None:
        # Wrapper returns a JSON with unexpected status value
        client = _make_client(json.dumps({"status": "unknown_status"}))
        resp = await execute_script(client, "return 1;")
        assert resp["status"] == "error"
        assert resp["error"]["code"] == "PARSE_ERROR"

    @pytest.mark.asyncio
    async def test_timeout_detail_includes_sent_script(self) -> None:
        client = MagicMock(spec=FmodTcpClient)
        client.eval_js = AsyncMock(side_effect=TimeoutError())
        resp = await execute_script(client, "while(true){}")
        assert "sent_script" in resp["error"]["detail"]
