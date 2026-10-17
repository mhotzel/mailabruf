# SPDX-License-Identifier: GPL-2.0-only
"""Console output: results go to stdout, warnings and errors to stderr."""

import sys
from typing import TextIO


class Output:
    def __init__(
        self,
        quiet: bool = False,
        verbose: bool = False,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self.quiet = quiet
        self.verbose = verbose
        self._stdout = stdout
        self._stderr = stderr

    def info(self, message: str) -> None:
        """Regular result line; suppressed with --quiet."""
        if not self.quiet:
            print(message, file=self._stdout or sys.stdout, flush=True)

    def detail(self, message: str) -> None:
        """Additional line shown only with --verbose."""
        if self.verbose and not self.quiet:
            print(message, file=self._stdout or sys.stdout, flush=True)

    def warning(self, message: str) -> None:
        print(f"warning: {message}", file=self._stderr or sys.stderr, flush=True)

    def error(self, message: str) -> None:
        print(f"error: {message}", file=self._stderr or sys.stderr, flush=True)
