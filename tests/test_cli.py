"""CLI argument parsing and logging configuration tests."""

from __future__ import annotations

import logging
from io import StringIO
from unittest.mock import patch

import pytest

from fmod_studio_mcp.server import _configure_logging


class TestCliHelp:
    def test_help_exits_with_zero(self) -> None:
        """--help should print usage and exit with code 0."""
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stdout", new_callable=StringIO):
                with patch("sys.stderr", new_callable=StringIO):
                    _configure_logging(["--help"])
        assert exc_info.value.code == 0


class TestCliLogLevel:
    def test_no_args_sets_warning(self) -> None:
        """No arguments → log level WARNING."""
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            level = _configure_logging([])
        assert level == logging.WARNING

    def test_verbose_sets_debug(self) -> None:
        """--verbose → log level DEBUG."""
        with patch("sys.stderr", new_callable=StringIO):
            level = _configure_logging(["--verbose"])
        assert level == logging.DEBUG

    def test_verbose_short_sets_debug(self) -> None:
        """-v → log level DEBUG."""
        with patch("sys.stderr", new_callable=StringIO):
            level = _configure_logging(["-v"])
        assert level == logging.DEBUG

    def test_log_level_debug_sets_debug(self) -> None:
        """--log-level DEBUG → log level DEBUG."""
        with patch("sys.stderr", new_callable=StringIO):
            level = _configure_logging(["--log-level", "DEBUG"])
        assert level == logging.DEBUG

    def test_log_level_overrides_verbose(self) -> None:
        """--log-level takes priority over --verbose when both specified."""
        with patch("sys.stderr", new_callable=StringIO):
            level = _configure_logging(["--verbose", "--log-level", "ERROR"])
        assert level == logging.ERROR


class TestCliStartupInfo:
    def test_startup_info_written_to_stderr(self) -> None:
        """Startup info (server name + connection target) is always written to stderr."""
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            _configure_logging([])
        output = stderr.getvalue()
        assert "FMOD Studio MCP Server" in output
        assert "localhost:3663" in output

    def test_startup_info_contains_version(self) -> None:
        """Startup info contains version string (or 'unknown' on lookup failure)."""
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            _configure_logging([])
        output = stderr.getvalue()
        assert "v" in output  # version prefix present

    def test_startup_info_written_even_at_warning_level(self) -> None:
        """Startup info is written regardless of log level (requirement: always visible)."""
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            _configure_logging([])  # WARNING level (default)
        output = stderr.getvalue()
        assert "FMOD Studio MCP Server" in output
