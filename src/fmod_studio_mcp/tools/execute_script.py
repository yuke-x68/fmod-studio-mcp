"""execute_script — Execute arbitrary JavaScript in FMOD Studio with security gating."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Callable

from fmod_studio_mcp.bridge import FmodTcpClient
from fmod_studio_mcp.bridge.security import scan_script

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Maximum characters of sent_script included in blocked/error detail payloads.
_SENT_SCRIPT_MAX_LEN = 500

# Server-side JS wrapper (M1 fix):
# Wraps user script in an IIFE with try-catch + JSON.stringify so that FMOD
# Studio *always* returns valid JSON, preventing TimeoutError from non-JSON
# responses caused by scripts with side effects or print-style output.
#
# Structure:
#   outer IIFE → try { inner IIFE (user code) → JSON.stringify ok result }
#                catch(e) { JSON.stringify error result }
#
# The inner IIFE isolates user code's return value in __result, allowing
# "return X;" at top level of the user script.
_JS_WRAPPER = (
    "(function() {{ "
    "try {{ "
    'var __result = (function() {{ {user_script} }})(); '
    'return JSON.stringify({{status: "ok", result: __result === undefined ? null : __result}}); '
    "}} catch(e) {{ "
    'return JSON.stringify({{status: "error", error: e.toString()}}); '
    "}} "
    "}})()"
)


def _truncate(s: str, max_len: int = _SENT_SCRIPT_MAX_LEN) -> str:
    """Truncate a string for inclusion in error details."""
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"... [{len(s) - max_len} chars truncated]"


async def execute_script(
    client: FmodTcpClient,
    script: str,
    timeout_ms: int = 5000,
) -> dict:
    """Execute JavaScript in FMOD Studio with security scanning and structured response.

    Execution flow:
      1. Security scan via scan_script() — blocked scripts never reach FMOD.
      2. Wrap user script in try-catch + JSON.stringify envelope.
      3. Call eval_js() with the wrapped script.
      4. Parse the JSON response and classify errors.

    Args:
        client: Active FmodTcpClient instance.
        script: JavaScript source code to execute.
        timeout_ms: Execution timeout in milliseconds (default 5000).

    Returns:
        Structured response dict (never raises):

        - ok:      ``{"status": "ok", "result": <any>}``
        - blocked: ``{"status": "blocked", "error": {"code": "BLOCKED", ...}}``
        - error:   ``{"status": "error", "error": {"code": <CODE>, ...}}``

        Error codes: ``SYNTAX_ERROR``, ``RUNTIME_ERROR``, ``TIMEOUT``,
        ``PARSE_ERROR``, ``CONNECTION_ERROR``.
    """
    # ------------------------------------------------------------------
    # Step 1: Security gate
    # ------------------------------------------------------------------
    blocked_reasons = scan_script(script)
    if blocked_reasons:
        logger.warning(
            "execute_script blocked: %d pattern(s) matched", len(blocked_reasons)
        )
        return {
            "status": "blocked",
            "error": {
                "code": "BLOCKED",
                "message": (
                    f"Script blocked by security policy "
                    f"({len(blocked_reasons)} pattern(s) matched)"
                ),
                "detail": {
                    "sent_script": _truncate(script),
                    "blocked_patterns": blocked_reasons,
                },
            },
        }

    # ------------------------------------------------------------------
    # Step 2: Wrap user script
    # ------------------------------------------------------------------
    wrapped = _JS_WRAPPER.format(user_script=script)
    timeout_s = timeout_ms / 1000.0
    logger.debug(
        "execute_script: sending %d-char script (timeout=%.1fs)", len(script), timeout_s
    )

    # ------------------------------------------------------------------
    # Step 3: TCP execution
    # ------------------------------------------------------------------
    try:
        raw = await client.eval_js(wrapped, timeout_s=timeout_s)
    except TimeoutError:
        logger.warning("execute_script: timeout after %.1fs", timeout_s)
        return {
            "status": "error",
            "error": {
                "code": "TIMEOUT",
                "message": f"Script did not respond within {timeout_ms}ms",
                "detail": {"sent_script": _truncate(script)},
            },
        }
    except ConnectionError as exc:
        logger.warning("execute_script: connection error: %s", exc)
        return {
            "status": "error",
            "error": {
                "code": "CONNECTION_ERROR",
                "message": str(exc),
                "detail": {"sent_script": _truncate(script)},
            },
        }

    # ------------------------------------------------------------------
    # Step 4: Parse response
    # ------------------------------------------------------------------
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("execute_script: PARSE_ERROR — raw=%r", raw[:200])
        return {
            "status": "error",
            "error": {
                "code": "PARSE_ERROR",
                "message": "Failed to parse FMOD response as JSON",
                "detail": {
                    "sent_script": _truncate(script),
                    "raw_response": raw[:500],
                },
            },
        }

    # ------------------------------------------------------------------
    # Step 5: Map JS wrapper status to structured response
    # ------------------------------------------------------------------
    js_status = data.get("status")

    if js_status == "ok":
        return {"status": "ok", "result": data.get("result")}

    if js_status == "error":
        js_error = str(data.get("error", "Unknown error"))
        # Distinguish SyntaxError (parse-time) from other runtime errors.
        # The JS try-catch in the wrapper catches runtime exceptions;
        # SyntaxErrors propagate as "SyntaxError: ..." in e.toString().
        # NOTE: SyntaxError classification assumes FMOD's embedded JS engine wraps
        # parse-time errors as runtime exceptions caught by the outer IIFE try-catch.
        # If FMOD returns a raw non-JSON string for syntax errors, classification
        # falls to PARSE_ERROR (step 4) instead of SYNTAX_ERROR. This degrades
        # debuggability but causes no security or data-loss consequence.
        # Verified only via mock test; validate against real FMOD Studio if needed.
        code = "SYNTAX_ERROR" if "SyntaxError" in js_error else "RUNTIME_ERROR"
        logger.debug("execute_script: %s — %s", code, js_error[:200])
        return {
            "status": "error",
            "error": {
                "code": code,
                "message": js_error,
                "detail": {"sent_script": _truncate(script)},
            },
        }

    # Unexpected wrapper response (should not happen in normal operation)
    logger.warning("execute_script: unexpected js_status=%r", js_status)
    return {
        "status": "error",
        "error": {
            "code": "PARSE_ERROR",
            "message": f"Unexpected response status from wrapper: {js_status!r}",
            "detail": {
                "sent_script": _truncate(script),
                "raw_response": raw[:500],
            },
        },
    }


def register(
    mcp_server: "FastMCP",
    get_client: Callable[[], tuple[FmodTcpClient, object]],
) -> None:
    """Register execute_script tool with the MCP server."""

    @mcp_server.tool()
    async def execute_script_tool(
        script: str,
        timeout_ms: int = 5000,
    ) -> str:
        """Execute arbitrary JavaScript in FMOD Studio with security scanning.

        Runs the provided JavaScript code in FMOD Studio's scripting terminal
        (TCP:3663).  The script is scanned against a security policy before
        execution — blocked scripts are never sent to FMOD Studio.

        Currently permitted top-level APIs include:
          studio.project.save(), studio.project.build(),
          studio.project.importAudioFile(), and read-only inspection methods.

        Args:
            script: JavaScript source code to execute.
            timeout_ms: Execution timeout in milliseconds (default 5000).

        Returns:
            JSON string with one of:
              {"status": "ok", "result": <value>}
              {"status": "blocked", "error": {"code": "BLOCKED", ...}}
              {"status": "error", "error": {"code": <CODE>, "message": ..., "detail": {...}}}
        """
        client, _ = get_client()
        result = await execute_script(client, script, timeout_ms)
        return json.dumps(result)
