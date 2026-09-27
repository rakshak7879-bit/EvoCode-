"""Dependency-free terminal formatting for Evo Code.

Color is enabled only on a TTY and can be disabled with ``NO_COLOR`` or
``--no-color``. Output stays readable when redirected to a file or used in tests.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import TextIO

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class Style:
    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"
    DIM = "\x1b[2m"
    RED = "\x1b[31m"
    GREEN = "\x1b[32m"
    YELLOW = "\x1b[33m"
    BLUE = "\x1b[34m"
    MAGENTA = "\x1b[35m"
    CYAN = "\x1b[36m"
    WHITE = "\x1b[37m"
    BRIGHT_BLACK = "\x1b[90m"
    BRIGHT_RED = "\x1b[91m"
    BRIGHT_GREEN = "\x1b[92m"
    BRIGHT_YELLOW = "\x1b[93m"
    BRIGHT_BLUE = "\x1b[94m"
    BRIGHT_MAGENTA = "\x1b[95m"
    BRIGHT_CYAN = "\x1b[96m"


def visible_len(value: str) -> int:
    return len(_ANSI.sub("", value))


class Console:
    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        error_stream: TextIO | None = None,
        color: bool | None = None,
    ) -> None:
        self.stream = stream or sys.stdout
        self.error_stream = error_stream or sys.stderr
        #: Set when readline line editing is active: prompt colors are then marked non-printing.
        self.line_editing = False
        self.is_tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.color = self.is_tty and "NO_COLOR" not in os.environ if color is None else color
        encoding = getattr(self.stream, "encoding", None) or "utf-8"
        self.unicode = encoding.lower().startswith("utf")

    @property
    def width(self) -> int:
        return max(60, min(120, shutil.get_terminal_size((92, 24)).columns))

    def glyph(self, fancy: str, plain: str) -> str:
        """Unicode glyph when the stream supports it, ASCII otherwise."""
        return fancy if self.unicode else plain

    def fit(self, text: str, width: int) -> str:
        """Truncate plain text to ``width`` visible characters."""
        if width <= 1 or len(text) <= width:
            return text if width > 1 else text[:1]
        return text[: width - 1] + self.glyph("…", ".")

    def paint(self, value: object, *styles: str) -> str:
        text = str(value)
        return f"{''.join(styles)}{text}{Style.RESET}" if self.color and styles else text

    def write(self, value: object = "", *, end: str = "\n", error: bool = False) -> None:
        target = self.error_stream if error else self.stream
        print(value, end=end, file=target, flush=True)

    def rule(self, title: str | None = None) -> None:
        char = "─" if self.unicode else "-"
        if not title:
            self.write(self.paint(char * min(self.width, 92), Style.BRIGHT_BLACK))
            return
        label = f" {title} "
        remaining = max(2, min(self.width, 92) - len(label))
        self.write(self.paint(f"{label}{char * remaining}", Style.BRIGHT_BLACK))

    def heading(self, title: str, subtitle: str | None = None) -> None:
        self.write()
        self.write(self.paint(title, Style.BOLD, Style.WHITE))
        if subtitle:
            self.write(self.paint(subtitle, Style.DIM))

    def banner(self) -> None:
        brain = "◉" if self.unicode else "*"
        self.write()
        self.write(self.paint(f"  {brain}  E V O   C O D E", Style.BOLD, Style.BRIGHT_CYAN))
        self.write(self.paint("     Your AI Code Brain · verified source-level intelligence", Style.DIM))
        self.write()

    def success(self, message: str) -> None:
        self.write(f"{self.paint('✓' if self.unicode else 'OK', Style.BRIGHT_GREEN)} {message}")

    def warning(self, message: str) -> None:
        self.write(f"{self.paint('!' if not self.unicode else '▲', Style.BRIGHT_YELLOW)} {message}")

    def error(self, message: str) -> None:
        self.write(f"{self.paint('x' if not self.unicode else '✗', Style.BRIGHT_RED)} {message}", error=True)

    def info(self, message: str) -> None:
        self.write(f"{self.paint('i' if not self.unicode else '◆', Style.BRIGHT_CYAN)} {message}")

    def key_value(self, key: str, value: object, *, indent: int = 0) -> None:
        self.write(f"{' ' * indent}{self.paint(f'{key}:', Style.DIM)} {value}")

    def wrapped(self, text: str, *, indent: int = 0, subsequent: int | None = None, style: str | None = None) -> None:
        initial = " " * indent
        follow = " " * (subsequent if subsequent is not None else indent)
        raw_lines = text.splitlines() or [""]
        for raw in raw_lines:
            if not raw:
                self.write()
                continue
            wrapped = textwrap.wrap(
                raw,
                width=max(30, self.width - indent),
                initial_indent=initial,
                subsequent_indent=follow,
                replace_whitespace=False,
            ) or [initial]
            for line in wrapped:
                self.write(self.paint(line, style) if style else line)

    def table(self, headers: Sequence[str], rows: Iterable[Sequence[object]], *, max_widths: Sequence[int] | None = None) -> None:
        data = [[str(cell) for cell in row] for row in rows]
        if not data:
            self.write(self.paint("(none)", Style.DIM))
            return
        widths = [len(header) for header in headers]
        for row in data:
            for index, cell in enumerate(row[: len(widths)]):
                widths[index] = max(widths[index], visible_len(cell))
        if max_widths:
            widths = [min(width, max_widths[i]) for i, width in enumerate(widths)]
        available = self.width - 3 * (len(widths) - 1)
        while sum(widths) > available and max(widths) > 12:
            index = max(range(len(widths)), key=widths.__getitem__)
            widths[index] -= 1

        def format_row(row: Sequence[str]) -> str:
            cells = []
            for index, width in enumerate(widths):
                value = row[index] if index < len(row) else ""
                plain = _ANSI.sub("", value)
                if len(plain) > width:
                    value = plain[: max(1, width - 1)] + ("…" if self.unicode else ".")
                cells.append(value + " " * max(0, width - visible_len(value)))
            return "   ".join(cells).rstrip()

        self.write(self.paint(format_row(list(headers)), Style.BOLD, Style.DIM))
        self.write(self.paint("─" * min(self.width, visible_len(format_row(list(headers)))), Style.BRIGHT_BLACK))
        for row in data:
            self.write(format_row(row))

    def prompt(self, label: str) -> str | None:
        """Read one line; ``None`` at end of input (Ctrl-D or a closed pipe)."""
        text = self.paint(label, Style.BOLD, Style.BRIGHT_CYAN)
        if self.line_editing:
            # \001 … \002 tell readline/libedit that escape codes take no width,
            # so cursor movement and history recall stay aligned.
            text = _ANSI.sub(lambda match: f"\001{match.group(0)}\002", text)
        try:
            return input(text)
        except EOFError:
            return None

    def clear(self) -> None:
        if self.color:
            self.write("\x1b[2J\x1b[H", end="")


def enable_line_editing(history_file: Path, commands: Sequence[str]) -> Callable[[], None] | None:
    """Arrow keys, persistent history and tab completion for interactive prompts.

    Works with GNU readline and macOS libedit. Returns a function that saves the
    history, or ``None`` when no readline implementation is available.
    """
    try:
        import readline
    except ImportError:  # e.g. Windows without pyreadline
        return None
    try:
        readline.read_history_file(str(history_file))
    except OSError:
        pass
    readline.set_history_length(1000)
    words = sorted(set(commands))

    def complete(text: str, state: int) -> str | None:
        if readline.get_line_buffer().lstrip().count(" "):
            return None  # only the command word is completed
        matches = [word for word in words if word.startswith(text)]
        return f"{matches[state]} " if state < len(matches) else None

    readline.set_completer(complete)
    readline.set_completer_delims(" \t")
    libedit = getattr(readline, "backend", "") == "editline" or "libedit" in (readline.__doc__ or "")
    readline.parse_and_bind("bind ^I rl_complete" if libedit else "tab: complete")

    def save() -> None:
        try:
            history_file.parent.mkdir(parents=True, exist_ok=True)
            readline.write_history_file(str(history_file))
        except OSError:
            pass

    return save
