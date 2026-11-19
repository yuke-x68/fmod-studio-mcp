"""Tests for bridge/security.py — static pattern-matching gate."""

from __future__ import annotations

import pytest

from fmod_studio_mcp.bridge.security import scan_script


class TestBlockedPatterns:
    """Each blocked API pattern must be caught by scan_script()."""

    def test_trigger_action_blocked(self) -> None:
        reasons = scan_script("studio.window.triggerAction(6)")
        assert any("triggerAction" in r for r in reasons)

    def test_trigger_action_no_args_blocked(self) -> None:
        # All forms of triggerAction() must be blocked (R1: full block policy)
        reasons = scan_script("obj.triggerAction()")
        assert any("triggerAction" in r for r in reasons)

    def test_system_start_blocked(self) -> None:
        reasons = scan_script("studio.system.start()")
        assert any("start" in r for r in reasons)

    def test_system_stop_blocked(self) -> None:
        reasons = scan_script("studio.system.stop()")
        assert any("stop" in r for r in reasons)

    def test_system_record_blocked(self) -> None:
        reasons = scan_script("studio.system.record()")
        assert any("record" in r for r in reasons)

    def test_project_close_blocked(self) -> None:
        reasons = scan_script("studio.project.close()")
        assert any("close" in r for r in reasons)

    def test_project_delete_blocked(self) -> None:
        reasons = scan_script("studio.project.delete(item)")
        assert any("delete" in r for r in reasons)

    def test_project_new_blocked(self) -> None:
        reasons = scan_script("studio.project.new()")
        assert any("new" in r for r in reasons)

    def test_file_open_blocked(self) -> None:
        # .open() covers FMOD File API (M2 decision)
        reasons = scan_script("File.open('path/to/file')")
        assert any("open" in r for r in reasons)

    def test_fileio_open_blocked(self) -> None:
        reasons = scan_script("FileIO.open('/etc/passwd')")
        assert any("open" in r for r in reasons)


class TestAllowedScripts:
    """Scripts using permitted APIs must pass the security gate."""

    def test_list_events_allowed(self) -> None:
        script = (
            "var events = [];"
            "studio.project.model.Event.findInstances().forEach(function(e) {"
            "  events.push(e.getPath());"
            "});"
            "return events;"
        )
        assert scan_script(script) == []

    def test_project_save_allowed(self) -> None:
        assert scan_script("studio.project.save()") == []

    def test_project_build_allowed(self) -> None:
        assert scan_script("studio.project.build()") == []

    def test_import_audio_file_allowed(self) -> None:
        assert scan_script("studio.project.importAudioFile('explosion.wav')") == []

    def test_simple_arithmetic_allowed(self) -> None:
        assert scan_script("return 1 + 2;") == []

    def test_empty_script_allowed(self) -> None:
        assert scan_script("") == []


class TestBypassAttempts:
    """Common bypass attempts (whitespace/newline insertion) must be blocked."""

    def test_trigger_action_with_whitespace_blocked(self) -> None:
        # Whitespace inserted between name and paren — still blocked
        reasons = scan_script("triggerAction  (6)")
        assert any("triggerAction" in r for r in reasons)

    def test_system_start_with_whitespace_blocked(self) -> None:
        # studio.system. start() — whitespace after dot
        reasons = scan_script("studio.system. start()")
        assert any("start" in r for r in reasons)

    def test_system_stop_with_newline_blocked(self) -> None:
        # studio.system.\nstop() — newline after dot
        reasons = scan_script("studio.system.\nstop()")
        assert any("stop" in r for r in reasons)

    def test_file_open_with_whitespace_blocked(self) -> None:
        # File. open() — whitespace between dot and method
        reasons = scan_script("File. open('path')")
        assert any("open" in r for r in reasons)

    def test_file_open_with_newline_blocked(self) -> None:
        # File.\nopen() — newline between dot and method
        reasons = scan_script("File.\nopen('path')")
        assert any("open" in r for r in reasons)

    def test_project_close_with_whitespace_blocked(self) -> None:
        reasons = scan_script("studio.project. close()")
        assert any("close" in r for r in reasons)


class TestKnownLimitations:
    """Variable-indirect calls are out of scope for static analysis — document expected behavior."""

    def test_variable_indirect_not_blocked(self) -> None:
        # This is a documented limitation: var fn = studio.system.start; fn();
        # Static regex cannot catch this. The test documents this as expected.
        script = "var fn = studio.system.start; fn();"
        # We do NOT assert blocked here — this is the known limitation.
        # The important thing is it doesn't crash:
        result = scan_script(script)
        assert isinstance(result, list)

    def test_multiple_patterns_all_reported(self) -> None:
        # Script with multiple violations should report all of them
        script = "studio.system.start(); studio.system.stop();"
        reasons = scan_script(script)
        assert len(reasons) >= 2
