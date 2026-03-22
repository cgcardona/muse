"""Argparse-compatible CliRunner replacement for Muse test suite.

Replaces ``typer.testing.CliRunner`` so tests can call ``runner.invoke(cli,
args)`` without modification after the typer → argparse migration.  The first
argument (``cli``) is always ``None`` (a stub) after migration; it is accepted
but ignored, and ``muse.cli.app.main`` is always the target.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import traceback

from muse.cli.app import main

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences — typer's CliRunner did this automatically."""
    return _ANSI_ESCAPE.sub("", text)


class _StdinWithBuffer(io.StringIO):
    """Text-mode stdin backed by StringIO, with a ``.buffer`` BytesIO sibling.

    Some plumbing commands (e.g. ``unpack-objects``) read raw bytes from
    ``sys.stdin.buffer``.  Plain ``StringIO`` has no ``.buffer``; subclassing
    it lets us assign to ``sys.stdin`` without a type annotation workaround
    while still exposing the binary-read surface.
    """

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.buffer = io.BytesIO(text.encode())

    def isatty(self) -> bool:
        return False


class _StdoutCapture(io.StringIO):
    """Text-mode stdout backed by StringIO, with a ``.buffer`` BytesIO sibling.

    Some plumbing commands (e.g. ``cat-object``) write raw bytes to
    ``sys.stdout.buffer``.  Subclassing ``StringIO`` makes this assignable to
    ``sys.stdout`` (and passable to ``contextlib.redirect_stdout``) without
    any type annotation workaround.  Binary output is decoded and appended to
    the text output in ``getvalue()``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.buffer = io.BytesIO()

    def isatty(self) -> bool:
        return False

    def getvalue(self) -> str:
        text_out = super().getvalue()
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
        _cli: None,
        args: list[str],
        catch_exceptions: bool = True,
        input: str | None = None,
        env: dict[str, str] | None = None,
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
        # _StdinWithBuffer subclasses StringIO so the assignment is well-typed.
        orig_stdin = sys.stdin
        if input is not None:
            sys.stdin = _StdinWithBuffer(input)

        try:
            with (
                contextlib.redirect_stdout(stdout_cap),
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
