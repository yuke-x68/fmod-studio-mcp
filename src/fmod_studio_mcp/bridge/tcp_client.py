"""FmodTcpClient — TCP:3663 connection manager for FMOD Studio Scripting Terminal (Section 2.3, 5.3)."""

from __future__ import annotations

import asyncio
import json
import logging
import re

from fmod_studio_mcp.bridge.config import BridgeConfig

logger = logging.getLogger(__name__)

# Pre-compiled regexes for FMOD Scripting Terminal response parsing (V4 fix)
_OUT_RE = re.compile(r"^out\(\):\s*")
_NOISE_RE = re.compile(r"^[a-z]+\(\):\s*")


def extract_json_from_fmod_response(text: str) -> str | None:
    """Extract JSON content from FMOD Scripting Terminal response text.

    Handles FMOD's prefix format:
    - ``out(): VALUE`` — strip prefix, extract VALUE (the actual response)
    - ``log(): MESSAGE`` and other prefixes — discard (FMOD internal logging noise)

    Args:
        text: Raw decoded text from FMOD Studio TCP response.

    Returns:
        Extracted JSON string if valid JSON found, None otherwise.
    """
    # First attempt: parse the full text as-is
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("status") == "error":
            logger.warning("FMOD error response: %s", text)
        return text
    except json.JSONDecodeError:
        pass

    # Second attempt: filter FMOD prefix lines and extract content
    extracted_lines = []
    for line in text.splitlines():
        m = _OUT_RE.match(line)
        if m:
            content = line[m.end():]
            if content:
                extracted_lines.append(content)
        elif _NOISE_RE.match(line):
            pass  # Discard log(): and other FMOD noise lines
        else:
            extracted_lines.append(line)
    filtered = "\n".join(extracted_lines).strip()
    if not filtered:
        return None
    try:
        parsed = json.loads(filtered)
        if isinstance(parsed, dict) and parsed.get("status") == "error":
            logger.warning("FMOD error response: %s", filtered)
        return filtered
    except json.JSONDecodeError:
        return None


class FmodTcpClient:
    """TCP:3663 connection manager for FMOD Studio Scripting Terminal.

    IMPORTANT: All eval_js() calls are serialized via asyncio.Lock.
    TCP:3663 is a single-stream REPL — concurrent sends corrupt the stream.
    """

    def __init__(self, config: BridgeConfig) -> None:
        self._config = config
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Establish TCP connection to FMOD Studio."""
        host = self._config.resolve_host()
        port = self._config.port
        logger.debug("Connecting to FMOD Studio at %s:%d", host, port)
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=self._config.connect_timeout_s,
            )
            logger.info("Connected to FMOD Studio at %s:%d", host, port)
            await self._drain_welcome_banner()
        except Exception:
            self._reader = None
            self._writer = None
            raise

    async def _drain_welcome_banner(self) -> None:
        """Drain the welcome banner sent by FMOD Studio Scripting Terminal.

        FMOD Studio sends a non-JSON welcome message immediately after TCP connect:
        ``log(): Connected to FMOD Studio on <host>:<port>``

        This must be consumed before any eval_js() calls to prevent the banner
        from contaminating the first _send_and_receive() response buffer.
        If no banner arrives within 1 second (e.g. a future FMOD version omits it),
        the TimeoutError is silently ignored and the connection proceeds normally.
        """
        assert self._reader is not None
        try:
            banner = await asyncio.wait_for(
                self._reader.readline(),
                timeout=1.0,
            )
            if banner:
                logger.debug(
                    "Drained welcome banner: %s",
                    banner.decode("utf-8", errors="replace").strip(),
                )
        except asyncio.TimeoutError:
            logger.debug("No welcome banner received (timeout, continuing)")

    async def disconnect(self) -> None:
        """Close TCP connection."""
        if self._writer is not None:
            try:
                self._writer.close()
                try:
                    await self._writer.wait_closed()
                except Exception:
                    pass
            except OSError:
                pass
            self._reader = None
            self._writer = None
            logger.debug("Disconnected from FMOD Studio")

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def eval_js(self, js_code: str, timeout_s: float | None = None) -> str:
        """Send JavaScript code and receive evaluation result.

        Serialized via asyncio.Lock — concurrent calls are queued, not interleaved.

        Args:
            js_code: JavaScript code to evaluate.
            timeout_s: Override timeout (defaults to config.timeout_s).

        Returns:
            Evaluation result as string.

        Raises:
            ConnectionError: If FMOD Studio is not reachable after retries.
            TimeoutError: If response not received within timeout.
        """
        async with self._lock:
            for attempt in range(self._config.max_retries + 1):
                try:
                    if not self.is_connected:
                        await self.connect()
                    return await self._send_and_receive(js_code, timeout_s)
                except (ConnectionError, OSError) as e:
                    if attempt < self._config.max_retries:
                        logger.warning(
                            "Connection error (attempt %d/%d): %s. Retrying...",
                            attempt + 1,
                            self._config.max_retries + 1,
                            e,
                        )
                        await asyncio.sleep(self._config.retry_delay_s)
                        await self.disconnect()
                    else:
                        host = self._config.resolve_host()
                        raise ConnectionError(
                            f"FMOD Studio not reachable on {host}:{self._config.port}. "
                            "Ensure FMOD Studio is running."
                        ) from e
        raise ConnectionError("Unexpected exit from retry loop")  # pragma: no cover

    async def health_check(self) -> bool:
        """Check if FMOD Studio is responsive by evaluating a trivial expression."""
        try:
            result = await asyncio.wait_for(self.eval_js("1+1"), timeout=3.0)
            return result.strip() == "2"
        except Exception:
            return False

    async def _send_and_receive(self, js_code: str, timeout_s: float | None) -> str:
        """Send JS code and accumulate response until valid JSON is received.

        Accumulates read() chunks and delegates JSON extraction to
        ``extract_json_from_fmod_response()`` on each iteration.
        Returns when JSON parse succeeds or timeout is exceeded.
        """
        timeout = timeout_s if timeout_s is not None else self._config.timeout_s
        assert self._reader is not None
        assert self._writer is not None
        logger.debug("Sending JS (first 100 chars): %s", js_code[:100])
        self._writer.write((js_code + "\n").encode("utf-8"))
        await self._writer.drain()
        buffer = b""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            remaining = deadline - loop.time()
            chunk = await asyncio.wait_for(
                self._reader.read(self._config.recv_buffer_size),
                timeout=remaining,
            )
            if not chunk:
                raise ConnectionError("FMOD Studio closed the connection")
            buffer += chunk
            logger.debug(
                "Received chunk: %d bytes, content: %s",
                len(chunk),
                chunk[:100].decode("utf-8", errors="replace"),
            )
            try:
                # Strip null bytes (\x00) that FMOD appends after \r\n\r\n terminators
                text = buffer.decode("utf-8").replace("\x00", "").strip()
            except UnicodeDecodeError:
                logger.debug("UTF-8 decode failed, accumulating")
                continue
            result = extract_json_from_fmod_response(text)
            if result is not None:
                logger.debug("JSON parse succeeded")
                return result
            logger.debug("JSON parse failed, accumulating")
        raise TimeoutError(f"No response within {timeout}s")
