"""Argparse-compatible CliRunner replacement for Muse test suite.

Replaces ``typer.testing.CliRunner`` so tests can call ``runner.invoke(cli,
args)`` without modification after the typer → argparse migration.  The first
argument (``cli``) is ignored; ``muse.cli.app.main`` is always the target.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import traceback
from typing import Any

from muse.cli.app import main

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences — typer's CliRunner did this automatically."""
    return _ANSI_ESCAPE.sub("", text)


class _StdinWithBuffer:
    """Text-mode stdin wrapper with a ``.buffer`` attribute for binary reads.

    Some plumbing commands (e.g. ``unpack-objects``) read raw bytes from
    ``sys.stdin.buffer``.  StringIO has no ``.buffer``, so we wrap it.
    """

    def __init__(self, text: str) -> None:
        self._text = io.StringIO(text)
        self.buffer = io.BytesIO(text.encode())

    def read(self, n: int = -1) -> str:
        return self._text.read(n)

    def readline(self) -> str:
        return self._text.readline()

    def isatty(self) -> bool:
        return False


class _StdoutCapture:
    """Text-mode stdout wrapper with a ``.buffer`` attribute for binary writes.

    Some plumbing commands (e.g. ``cat-object``) write raw bytes to
    ``sys.stdout.buffer``.  StringIO has no ``.buffer``, so we wrap it with
    a companion BytesIO and decode its bytes into the combined output.
    """

    def __init__(self) -> None:
        self._text = io.StringIO()
        self.buffer = io.BytesIO()

    # --- text-mode interface ------------------------------------------------
    def write(self, s: str) -> int:
        return self._text.write(s)

    def writelines(self, lines: list[str]) -> None:
        self._text.writelines(lines)

    def flush(self) -> None:
        self._text.flush()

    def isatty(self) -> bool:
        return False

    # --- value retrieval ----------------------------------------------------
    def getvalue(self) -> str:
        text_out = self._text.getvalue()
        bytes_out = self.buffer.getvalue()
        if bytes_out:
            try:
                text_out += bytes_out.decode("utf-8", errors="replace")
            except Exception:
                pass
        return text_out


def _restore_env(saved: dict[str, str | None]) -> None:
    """Restore environment variables to their pre-invoke state."""
    for k, orig in saved.items():
        if orig is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = orig


class InvokeResult:
    """Mirrors the fields that typer.testing.Result exposed."""

    def __init__(
        self,
        exit_code: int,
        output: str,
        stderr_output: str = "",
        stdout_bytes: bytes = b"",
    ) -> None:
        self.exit_code = exit_code
        self.output = output
        self.stdout = output
        self.stderr = stderr_output
        self.stdout_bytes = stdout_bytes
        self.exception: BaseException | None = None

    def __repr__(self) -> str:
        return f"InvokeResult(exit_code={self.exit_code}, output={self.output!r})"


class CliRunner:
    """Drop-in replacement for ``typer.testing.CliRunner``.

    Captures stdout and stderr, calls ``main(args)``, and returns an
    ``InvokeResult`` whose interface matches the typer equivalent closely
    enough for the existing test suite to run without changes.

    Honoured parameters:
    - ``env``: key/value pairs set in ``os.environ`` for the duration of the
      call and restored afterward.
    - ``input``: string fed to ``sys.stdin`` (needed by ``unpack-objects``).
    - ``catch_exceptions``: when False, exceptions propagate to the caller.
    """

    def invoke(
        self,
        _cli: Any,
        args: list[str],
        catch_exceptions: bool = True,
        input: str | None = None,
        env: dict[str, str] | None = None,
        **_kwargs: Any,
    ) -> InvokeResult:
        """Invoke ``main(args)`` and return captured output + exit code."""
        # Apply caller-supplied env overrides; restore originals when done.
        saved: dict[str, str | None] = {}
        if env:
            for k, v in env.items():
                saved[k] = os.environ.get(k)
                os.environ[k] = v

        stdout_cap = _StdoutCapture()
        stderr_buf = io.StringIO()
        exit_code = 0

        # Patch sys.stdin for commands that read from it (e.g. unpack-objects).
        # Use _StdinWithBuffer so sys.stdin.buffer is also available.
        orig_stdin = sys.stdin
        if input is not None:
            sys.stdin = _StdinWithBuffer(input)  # type: ignore[assignment]

        try:
            with (
                contextlib.redirect_stdout(stdout_cap),  # type: ignore[arg-type]
                contextlib.redirect_stderr(stderr_buf),
            ):
                main(list(args))
        except SystemExit as exc:
            raw = exc.code
            if isinstance(raw, int):
                exit_code = raw
            elif hasattr(raw, "value"):
                exit_code = int(raw.value)
            elif raw is None:
                exit_code = 0
            else:
                exit_code = int(raw)
        except Exception as exc:
            if not catch_exceptions:
                sys.stdin = orig_stdin
                _restore_env(saved)
                raise
            stderr_buf.write(traceback.format_exc())
            exit_code = 1
            result = InvokeResult(
                exit_code,
                _strip_ansi(stdout_cap.getvalue() + stderr_buf.getvalue()),
                _strip_ansi(stderr_buf.getvalue()),
                stdout_bytes=stdout_cap.buffer.getvalue(),
            )
            result.exception = exc
            sys.stdin = orig_stdin
            _restore_env(saved)
            return result
        finally:
            sys.stdin = orig_stdin
            _restore_env(saved)

        raw_bytes = stdout_cap.buffer.getvalue()
        combined = _strip_ansi(stdout_cap.getvalue() + stderr_buf.getvalue())
        return InvokeResult(
            exit_code,
            combined,
            _strip_ansi(stderr_buf.getvalue()),
            stdout_bytes=raw_bytes,
        )
