# -*- mode: python -*-
# Copyright (C) 2013-2026 Dan Meliza <dan@meliza.org>
# * SPDX-License-Identifier: GPL-2.0-only
"""The CLI's terminal output and questions: progress lines, summaries and prompts."""

import logging
import sys
import time
from collections import deque

log = logging.getLogger("nbank")


def interactive() -> bool:
    """True if the user can be asked questions."""
    return sys.stdin.isatty()


def ask(prompt: str, choices: str) -> str:
    """Asks the user to pick one of choices (single letters). Returns "" on EOF."""
    while True:
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            return ""
        if len(answer) == 1 and answer in choices:
            return answer


def log_summary(*counts: str, failed: int) -> None:
    """Logs the counts of a transfer, then the number that failed, as one line."""
    text = "Resources " + "; ".join([*counts, f"failed: {failed}"])
    if failed:
        log.error("%s (marked with ✗ above)", text)
    else:
        log.info("%s", text)


def human_size(nbytes: float) -> str:
    """Returns a byte count in decimal units, e.g. '1.2 GB'."""
    for unit in ("B", "kB", "MB", "GB"):
        if nbytes < 1000:
            return f"{nbytes:.0f} {unit}" if unit == "B" else f"{nbytes:.1f} {unit}"
        nbytes /= 1000
    return f"{nbytes:.1f} TB"


class Progress:
    """Reports progress reading a resource on one line of stderr, if it's a terminal.

    Call start() for each resource and pass the object as a progress callback. The rate
    shown is over the last `window` seconds. summary() gives the size and average rate
    whether or not stderr is a terminal. As a context manager, clears its line before
    nbank log messages.
    """

    interval = 0.2
    window = 5.0

    def __init__(self, stream=None):
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = self.stream.isatty()
        self._width = 0
        self.start("")

    def start(self, label: str, total: int | None = None) -> None:
        """Starts reporting on a resource called label, total bytes long if known."""
        self.label = label
        self.total = total
        self._path = None
        self._read: dict[str, int] = {}
        self._started = time.monotonic()

    @property
    def nbytes(self) -> int:
        """Returns the number of bytes of the resource read so far."""
        return sum(self._read.values())

    def summary(self) -> str:
        """Returns the size of the resource read so far and its average rate."""
        nbytes = self.nbytes
        elapsed = time.monotonic() - self._started
        if elapsed <= 0:
            return human_size(nbytes)
        return f"{human_size(nbytes)}, {human_size(nbytes / elapsed)}/s"

    def __call__(self, path: str, nbytes: int) -> None:
        self._read[path] = nbytes
        if not self.enabled:
            return
        now = time.monotonic()
        if path != self._path:
            self._path = path
            self._samples = deque()
        if self._samples and now - self._samples[-1][0] < self.interval:
            return
        self._samples.append((now, nbytes))
        # keep the newest sample that's at least a window old, as the baseline
        while len(self._samples) > 1 and now - self._samples[1][0] >= self.window:
            self._samples.popleft()
        whole = path == self.label
        text = f"  {self.label if whole else f'{self.label}/{path}'}  "
        text += human_size(nbytes)
        if whole and self.total:
            text += f" / {human_size(self.total)} ({100 * nbytes / self.total:.0f}%)"
        then, then_bytes = self._samples[0]
        if now > then:
            text += f"  {human_size((nbytes - then_bytes) / (now - then))}/s"
        self.clear()
        self.stream.write(text)
        self.stream.flush()
        self._width = len(text)

    def clear(self) -> None:
        """Erases the progress line, if there is one."""
        if self._width:
            self.stream.write("\r" + " " * self._width + "\r")
            self.stream.flush()
            self._width = 0

    def _before_log(self, record) -> bool:
        self.clear()
        return True

    def __enter__(self):
        for handler in log.handlers:
            handler.addFilter(self._before_log)
        return self

    def __exit__(self, *exc):
        self.clear()
        for handler in log.handlers:
            handler.removeFilter(self._before_log)


__all__: list[str] = []
