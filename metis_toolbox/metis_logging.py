"""
metis_logging.py — Shared Logging Setup
========================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Own how the stack writes logs to disk.
             One format, one directory, one rotating file per program.
             The entry point (felhaven.py) calls setup_logging() once at
             startup. Tool modules just call
             logging.getLogger("METIS.<name>") and inherit the config.

Format:      Fixed ` | `-delimited fields so Emanon can parse with a plain
             str.split — no regex. A trace-id slot can be appended later
             without breaking the parser (it only bumps the split count).

             2026-05-28 14:32:01 | INFO    | METIS.aether | fetch ok (0.4s)

             One record, one entry: a message is written on one line (its
             line breaks escaped), and a traceback's lines lose the
             delimiter, so text from a server can't start a line Emanon
             would read as an entry of its own (CONVENTIONS §12).

Idempotent:  Calling setup_logging() more than once will NOT attach
             duplicate handlers — guarded by a flag on the root logger.
             Without this guard, two callers double every log line.

Upstream:    felhaven.py (and any other program entry point)
Downstream:  emanon.py reads the files this writes

Requires:    stdlib only (logging, logging.handlers, sys, pathlib)

V0.02:       Audited 2026-07-26 — dropped an unused `import os`; added the used
             `sys` to Requires. emanon keeps its own DELIM / LOG_DIR, re-derived
             independently per §5 and lockstep-tested (kept two copies by choice
             this session, not collapsed). First tests.
"""

import logging
import logging.handlers
import sys
from pathlib import Path

# ── Where logs live ───────────────────────────────────────────────────────────
# Anchored to THIS file's directory, not sys.argv[0] — the lesson from the
# scribe/ammit path bug. logs/ sits beside the toolbox package, stable no
# matter how the program was launched.

LOG_DIR = Path(__file__).resolve().parent / "logs"

# ── Format ────────────────────────────────────────────────────────────────────
# %(levelname)-7s pads the level to 7 chars so the columns line up:
#   INFO   , WARNING, ERROR  , CRITICAL
# The delimiter is " | " with spaces — Emanon splits on exactly that.

_DELIM      = " | "
_LOG_FORMAT = f"%(asctime)s{_DELIM}%(levelname)-7s{_DELIM}%(name)s{_DELIM}%(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Rotation: keep the current file plus a few backups, cap each at ~1 MB.
_MAX_BYTES    = 1_000_000
_BACKUP_COUNT = 3

# Sentinel attribute we stamp on the root logger so we only configure once.
_GUARD_ATTR = "_metis_logging_configured"

# ── One record, one entry ─────────────────────────────────────────────────────
# Emanon opens the log in text mode and splits it with str.splitlines(), which
# ends a line at any of these ten characters, not only "\n". A message carrying
# one (a server's reply quoted in an error, a name read from a downloaded file)
# would start a second line, and Emanon reads any line with three delimiters as
# a real entry: level, logger and all. So a message is written on one line, each
# of these as its escape (\r, \x85, \u2028...), and the lines after a record's
# first (a traceback, a stack) keep their breaks but lose the delimiter. Only a
# record's first line can be read as an entry (CONVENTIONS §12, 2026-10-05).
_LINE_BREAKS = "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
_ONE_LINE = str.maketrans({c: ascii(c)[1:-1] for c in _LINE_BREAKS})
_CONTINUATION_DELIM = " ¦ "


class _OneEntryFormatter(logging.Formatter):
    """The shared format, with every record's entry held to its first line."""

    def formatMessage(self, record: logging.LogRecord) -> str:
        return super().formatMessage(record).translate(_ONE_LINE)

    def format(self, record: logging.LogRecord) -> str:
        head, sep, rest = super().format(record).partition("\n")
        return head + sep + rest.replace(_DELIM, _CONTINUATION_DELIM)


def setup_logging(program: str, level: int = logging.INFO) -> Path:
    """
    Configure logging for one program. Call once, at startup.

    Writes to LOG_DIR/<program>.log with rotation, using the shared
    delimited format. Safe to call again (idempotent) — repeat calls
    return the same path without adding handlers.

    Args:
        program: short name for this entry point, e.g. "felhaven".
                 Becomes the log filename (felhaven.log).
        level:   root logging level. INFO captures the fire/respond
                 stream; drop to DEBUG for noisier diagnostics.

    Returns:
        Path to the log file being written.
    """
    root = logging.getLogger()
    log_path = LOG_DIR / f"{program}.log"

    # Idempotency guard — the whole point of centralizing.
    if getattr(root, _GUARD_ATTR, False):
        return getattr(root, "_metis_log_path", log_path)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        # If we can't make the dir, fall back to console-only so the app
        # still runs — a logging failure must never take down the dashboard.
        print(f"[metis_logging] could not create {LOG_DIR}: {e}", file=sys.stderr)

    formatter = _OneEntryFormatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    handlers: list[logging.Handler] = []

    # File handler (rotating) — the surface Emanon reads.
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except Exception as e:
        print(f"[metis_logging] file handler failed: {e}", file=sys.stderr)

    # Console handler — handy while developing; harmless in production.
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    handlers.append(console)

    root.setLevel(level)
    for h in handlers:
        root.addHandler(h)

    # Stamp the guard so a second caller is a no-op.
    setattr(root, _GUARD_ATTR, True)
    setattr(root, "_metis_log_path", log_path)

    logging.getLogger("METIS.logging").info(
        f"logging online → {log_path} (level={logging.getLevelName(level)})"
    )
    return log_path


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = setup_logging("test")
    # Second call must NOT double the output — proves idempotency.
    setup_logging("test")
    log = logging.getLogger("METIS.demo")
    log.info("fired aether")
    log.info("aether ok (0.41s)")
    log.warning("aura timed out, using stale data")
    log.error("midas fetch failed: ConnectionError")
    print(f"\nWrote to: {p}")
    print("Each line above should appear ONCE. If doubled, the guard failed.")
