"""Tests for bridge/tcp_client.py — FmodTcpClient."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fmod_studio_mcp.bridge.config import BridgeConfig
from fmod_studio_mcp.bridge.tcp_client import FmodTcpClient


@pytest.fixture
def config() -> BridgeConfig:
    return BridgeConfig(
        host="127.0.0.1",
        port=3663,
        timeout_s=1.0,
        connect_timeout_s=1.0,
        max_retries=1,
        retry_delay_s=0.01,
    )


@pytest.fixture
def client(config: BridgeConfig) -> FmodTcpClient:
    return FmodTcpClient(config)


def _make_mock_stream() -> tuple[AsyncMock, MagicMock]:
    """Create mock StreamReader and StreamWriter."""
    reader = AsyncMock(spec=asyncio.StreamReader)
    reader.read.return_value = b""      # Default: no data for _send_and_receive
    reader.readline.return_value = b""  # Default: no banner (drain completes silently)
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.is_closing.return_value = False
    writer.wait_closed = AsyncMock()
    writer.drain = AsyncMock()
    return reader, writer


class TestFmodTcpClientConnect:
    @pytest.mark.asyncio
    async def test_connect_success(self, client: FmodTcpClient) -> None:
        mock_reader, mock_writer = _make_mock_stream()
        with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_open:
            mock_open.return_value = (mock_reader, mock_writer)
            await client.connect()
            assert client.is_connected

    @pytest.mark.asyncio
    async def test_connect_failure_closes_socket(self, client: FmodTcpClient) -> None:
        with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_open:
            mock_open.side_effect = ConnectionRefusedError("refused")
            with pytest.raises(ConnectionRefusedError):
                await client.connect()
            assert not client.is_connected

    @pytest.mark.asyncio
    async def test_connect_drains_welcome_banner(self, client: FmodTcpClient) -> None:
        """connect() calls _drain_welcome_banner after establishing connection."""
        mock_reader, mock_writer = _make_mock_stream()
        with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_open:
            mock_open.return_value = (mock_reader, mock_writer)
            with patch.object(client, "_drain_welcome_banner", new_callable=AsyncMock) as mock_drain:
                await client.connect()
                mock_drain.assert_called_once()


class TestFmodTcpClientDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_clears_socket(self, client: FmodTcpClient) -> None:
        mock_reader, mock_writer = _make_mock_stream()
        client._reader = mock_reader
        client._writer = mock_writer
        await client.disconnect()
        assert not client.is_connected
        mock_writer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self, client: FmodTcpClient) -> None:
        await client.disconnect()  # Should not raise
        assert not client.is_connected


class TestFmodTcpClientIsConnected:
    def test_not_connected_initially(self, client: FmodTcpClient) -> None:
        assert not client.is_connected

    def test_connected_after_socket_set(self, client: FmodTcpClient) -> None:
        _, mock_writer = _make_mock_stream()
        client._writer = mock_writer
        assert client.is_connected


class TestFmodTcpClientEvalJs:
    @pytest.mark.asyncio
    async def test_eval_js_success(self, client: FmodTcpClient) -> None:
        response_json = json.dumps({"status": "ok", "data": {"events": []}})
        mock_reader, mock_writer = _make_mock_stream()
        mock_reader.read = AsyncMock(return_value=response_json.encode("utf-8"))
        client._reader = mock_reader
        client._writer = mock_writer

        result = await client.eval_js("1+1")
        assert result == response_json

    @pytest.mark.asyncio
    async def test_eval_js_reconnects_on_oserror(self, client: FmodTcpClient) -> None:
        response_json = json.dumps({"status": "ok", "data": {}})
        call_count = 0

        async def mock_send_receive(js_code, timeout_s):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("broken pipe")
            return response_json

        _, mock_writer = _make_mock_stream()
        client._writer = mock_writer

        with patch.object(client, "_send_and_receive", side_effect=mock_send_receive):
            with patch.object(client, "connect", new_callable=AsyncMock):
                result = await client.eval_js("1+1")
                assert result == response_json
                assert call_count == 2  # First failed, second succeeded

    @pytest.mark.asyncio
    async def test_eval_js_raises_after_max_retries(self, client: FmodTcpClient) -> None:
        with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_open:
            mock_open.side_effect = ConnectionRefusedError("refused")
            with pytest.raises(ConnectionError, match="FMOD Studio not reachable"):
                await client.eval_js("1+1")

    @pytest.mark.asyncio
    async def test_eval_js_serialized_by_lock(self, client: FmodTcpClient) -> None:
        """Verify that concurrent eval_js calls are serialized."""
        call_order = []

        async def tracking_send_receive(js_code, timeout_s):
            call_order.append(f"start:{js_code}")
            await asyncio.sleep(0.01)
            call_order.append(f"end:{js_code}")
            return json.dumps({"status": "ok", "data": {}})

        _, mock_writer = _make_mock_stream()
        client._writer = mock_writer

        with patch.object(client, "_send_and_receive", side_effect=tracking_send_receive):
            await asyncio.gather(
                client.eval_js("A"),
                client.eval_js("B"),
            )

        # Verify calls are not interleaved
        assert call_order[0].startswith("start:")
        assert call_order[1].startswith("end:")
        assert call_order[2].startswith("start:")
        assert call_order[3].startswith("end:")


class TestFmodTcpClientDrainWelcomeBanner:
    @pytest.mark.asyncio
    async def test_drain_logs_and_discards_banner(self, client: FmodTcpClient) -> None:
        """Welcome banner is consumed via readline() and logged without raising."""
        mock_reader, mock_writer = _make_mock_stream()
        client._reader = mock_reader
        banner = b"log(): Connected to FMOD Studio on 127.0.0.1:3663\n"
        mock_reader.readline = AsyncMock(return_value=banner)

        await client._drain_welcome_banner()  # Should not raise
        mock_reader.readline.assert_called_once()

    @pytest.mark.asyncio
    async def test_drain_empty_response_does_not_raise(self, client: FmodTcpClient) -> None:
        """Empty readline (no banner data) does not raise."""
        mock_reader, mock_writer = _make_mock_stream()
        client._reader = mock_reader
        mock_reader.readline = AsyncMock(return_value=b"")

        await client._drain_welcome_banner()  # Should not raise

    @pytest.mark.asyncio
    async def test_drain_timeout_continues_normally(self, client: FmodTcpClient) -> None:
        """Timeout during banner drain is silently ignored and connection proceeds."""
        mock_reader, mock_writer = _make_mock_stream()
        client._reader = mock_reader

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            await client._drain_welcome_banner()  # Should not raise


class TestFmodTcpClientSendAndReceive:
    @pytest.mark.asyncio
    async def test_accumulates_chunks_until_valid_json(self, client: FmodTcpClient) -> None:
        response_json = json.dumps({"status": "ok", "data": {"x": 1}})
        # Split response into chunks to simulate TCP fragmentation
        chunk1 = response_json[:10].encode("utf-8")
        chunk2 = response_json[10:].encode("utf-8")

        mock_reader, mock_writer = _make_mock_stream()
        mock_reader.read = AsyncMock(side_effect=[chunk1, chunk2])
        client._reader = mock_reader
        client._writer = mock_writer

        result = await client._send_and_receive("test", timeout_s=1.0)
        assert json.loads(result) == {"status": "ok", "data": {"x": 1}}

    @pytest.mark.asyncio
    async def test_raises_timeout_when_no_valid_json(self, client: FmodTcpClient) -> None:
        mock_reader, mock_writer = _make_mock_stream()
        mock_reader.read = AsyncMock(return_value=b"incomplete")
        client._reader = mock_reader
        client._writer = mock_writer

        with pytest.raises(TimeoutError):
            await client._send_and_receive("test", timeout_s=0.05)

    @pytest.mark.asyncio
    async def test_raises_connection_error_on_empty_chunk(self, client: FmodTcpClient) -> None:
        mock_reader, mock_writer = _make_mock_stream()
        mock_reader.read = AsyncMock(return_value=b"")
        client._reader = mock_reader
        client._writer = mock_writer

        with pytest.raises(ConnectionError, match="closed the connection"):
            await client._send_and_receive("test", timeout_s=1.0)

    @pytest.mark.asyncio
    async def test_skips_log_prefix_lines_before_json(self, client: FmodTcpClient) -> None:
        """Non-JSON 'log():' prefix line is filtered; JSON response is returned."""
        banner_line = b"log(): Connected to FMOD Studio on 127.0.0.1:3663\n"
        json_response = json.dumps({"status": "ok", "data": {}}).encode("utf-8")
        response = banner_line + json_response

        mock_reader, mock_writer = _make_mock_stream()
        mock_reader.read = AsyncMock(return_value=response)
        client._reader = mock_reader
        client._writer = mock_writer

        result = await client._send_and_receive("test", timeout_s=1.0)
        assert json.loads(result) == {"status": "ok", "data": {}}

    @pytest.mark.asyncio
    async def test_skips_multiple_log_prefix_lines(self, client: FmodTcpClient) -> None:
        """Multiple 'log():' lines are all filtered; final JSON response is returned."""
        banner_lines = b"log(): line1\nlog(): line2\n"
        json_response = json.dumps({"result": 42}).encode("utf-8")
        response = banner_lines + json_response

        mock_reader, mock_writer = _make_mock_stream()
        mock_reader.read = AsyncMock(return_value=response)
        client._reader = mock_reader
        client._writer = mock_writer

        result = await client._send_and_receive("test", timeout_s=1.0)
        assert json.loads(result) == {"result": 42}

    @pytest.mark.asyncio
    async def test_strips_out_prefix_and_parses_json(self, client: FmodTcpClient) -> None:
        """FMOD wraps responses as 'out(): JSON'; strip prefix and parse the JSON content."""
        # Real FMOD response: out(): {"status":"ok","data":{}}\r\n\r\n
        json_payload = json.dumps({"status": "ok", "data": {}})
        response = (f"out(): {json_payload}\r\n\r\n").encode("utf-8")

        mock_reader, mock_writer = _make_mock_stream()
        mock_reader.read = AsyncMock(return_value=response)
        client._reader = mock_reader
        client._writer = mock_writer

        result = await client._send_and_receive("test", timeout_s=1.0)
        assert json.loads(result) == {"status": "ok", "data": {}}

    @pytest.mark.asyncio
    async def test_strips_out_prefix_multi_chunk(self, client: FmodTcpClient) -> None:
        """TCP fragmentation: 'out(): {JSON' in chunk 1, rest in chunk 2."""
        json_payload = json.dumps({"status": "ok", "data": {}})
        full = f"out(): {json_payload}\r\n\r\n"
        mid = len(full) // 2
        chunk1 = full[:mid].encode("utf-8")
        chunk2 = full[mid:].encode("utf-8")

        mock_reader, mock_writer = _make_mock_stream()
        mock_reader.read = AsyncMock(side_effect=[chunk1, chunk2])
        client._reader = mock_reader
        client._writer = mock_writer

        result = await client._send_and_receive("test", timeout_s=1.0)
        assert json.loads(result) == {"status": "ok", "data": {}}

    @pytest.mark.asyncio
    async def test_warns_on_fmod_error_response(self, client: FmodTcpClient) -> None:
        """FMOD error response triggers WARNING log."""
        error_response = json.dumps({"status": "error", "message": "not found"}).encode("utf-8")

        mock_reader, mock_writer = _make_mock_stream()
        mock_reader.read = AsyncMock(return_value=error_response)
        client._reader = mock_reader
        client._writer = mock_writer

        import logging
        with patch("fmod_studio_mcp.bridge.tcp_client.logger") as mock_logger:
            result = await client._send_and_receive("test", timeout_s=1.0)
            assert json.loads(result)["status"] == "error"
            mock_logger.warning.assert_called_once()
            assert "FMOD error response" in mock_logger.warning.call_args[0][0]


class TestFmodTcpClientHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_true_when_responsive(self, client: FmodTcpClient) -> None:
        with patch.object(client, "eval_js", new_callable=AsyncMock, return_value="2"):
            assert await client.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_false_when_wrong_response(self, client: FmodTcpClient) -> None:
        with patch.object(client, "eval_js", new_callable=AsyncMock, return_value="3"):
            assert await client.health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_false_on_exception(self, client: FmodTcpClient) -> None:
        with patch.object(client, "eval_js", new_callable=AsyncMock, side_effect=ConnectionError("down")):
            assert await client.health_check() is False
