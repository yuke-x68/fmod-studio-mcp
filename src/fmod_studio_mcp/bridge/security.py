"""Security scanner for execute_script — static pattern-matching gate."""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# BLOCKED_PATTERNS
# Each entry: (regex_pattern, reason_string)
#
# Design decisions (M2/R1/R2 from plan triad):
#
# R1 — triggerAction: FULL BLOCK adopted.
#   Original design blocked specific indices (6,12,4,11,10,50). Changed to
#   r"triggerAction\s*\(" (all arguments) because:
#   (a) Index semantics are FMOD-version-dependent; a "safe" index today may
#       trigger an unsafe action in a future FMOD version.
#   (b) Consistent with the gate principle: transport actions belong in
#       dedicated MCP tools, not in free-form scripts.
#
# M2 — .open() pattern: INCLUDED.
#   FMOD Studio's scripting environment exposes File.open() and related APIs
#   for arbitrary file read/write on the Windows host. Pattern uses \.\s*open
#   to also catch whitespace-inserted bypass attempts (e.g., "File. open()").
#   Known limitation: variable-indirect calls (var fn = File.open; fn(path))
#   are not caught by static analysis — this is by design.
#
# R2 — Combination attack analysis (studio.project.save/build/importAudioFile):
#   - studio.project.importAudioFile(path): Imports a file that already exists
#     on the Windows host. Cannot create or fabricate audio data.
#   - studio.project.save(): Persists the current project state to disk.
#   - studio.project.build(): Builds banks to the pre-configured output
#     directory. Cannot redirect the output path via JS alone.
#   Conclusion: The combination importAudioFile + save + build does not open
#   an attack path beyond what each tool does individually. An attacker would
#   need physical access to the Windows host to place a malicious audio file.
#   These three APIs remain in the "currently permitted" set.
# ---------------------------------------------------------------------------

BLOCKED_PATTERNS: list[tuple[str, str]] = [
    # Transport control — timeline trigger actions
    # Full block: any triggerAction call regardless of argument (see R1 note above).
    (
        r"triggerAction\s*\(",
        "triggerAction() — all argument forms blocked (use dedicated MCP transport tools)",
    ),
    # Transport start / stop / record
    (
        r"studio\s*\.\s*system\s*\.\s*start\s*\(",
        "studio.system.start() — transport start control",
    ),
    (
        r"studio\s*\.\s*system\s*\.\s*stop\s*\(",
        "studio.system.stop() — transport stop control",
    ),
    (
        r"studio\s*\.\s*system\s*\.\s*record\s*\(",
        "studio.system.record() — recording control",
    ),
    # Project lifecycle — destructive or irreversible
    (
        r"studio\s*\.\s*project\s*\.\s*close\s*\(",
        "studio.project.close() — closes the current project",
    ),
    (
        r"studio\s*\.\s*project\s*\.\s*delete\s*\(",
        "studio.project.delete() — destructive: permanently deletes a project item",
    ),
    (
        r"studio\s*\.\s*project\s*\.\s*new\s*\(",
        "studio.project.new() — replaces the current project in the editor",
    ),
    # File system access
    # Pattern: \.\s*open\s*\( catches File.open(), FileIO.open(), and whitespace
    # variants such as "File. open()" or "File.\nopen()".
    # See M2 decision note above.
    (
        r"\.\s*open\s*\(",
        ".open() — file system read/write access (FMOD File API and variants)",
    ),
]

# Pre-compiled for performance (called on every execute_script invocation)
_COMPILED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern), reason) for pattern, reason in BLOCKED_PATTERNS
]


def scan_script(script: str) -> list[str]:
    """Scan a JavaScript string for blocked API patterns.

    Args:
        script: JavaScript source code to inspect.

    Returns:
        List of human-readable block reasons.  An empty list means the script
        passed all checks and may proceed to execution.

    Notes:
        This is a *static* best-effort gate.  It catches direct API calls
        and common whitespace/newline bypass variants, but does NOT catch
        obfuscated or variable-indirect invocations such as::

            var fn = studio.system.start;
            fn();

        Dynamic dispatch evasion is out of scope for a regex scanner and is
        accepted as a known limitation (documented in the design book).
    """
    reasons: list[str] = []
    for compiled_re, reason in _COMPILED:
        if compiled_re.search(script):
            reasons.append(reason)
    return reasons
