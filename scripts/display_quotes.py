#!/usr/bin/env python3
"""Display and rotate the local Epigraph quote catalog on the frame."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from signal import SIGTERM, signal
from threading import Event, Lock
from typing import Literal

from catalog import Quote, load_catalog
from check_hardware import (
    ENCODER_A_GPIO,
    ENCODER_B_GPIO,
    ENCODER_SWITCH_GPIO,
    HEIGHT,
    PIR_GPIO,
    WIDTH,
    WaveshareDisplay,
    load_font,
)
from PIL import Image, ImageDraw, ImageFont, ImageOps

DEFAULT_ROTATION_MINUTES = 2
PIR_ACTIVITY_SECONDS = 15 * 60
MIN_ROTATION_MINUTES = 1
MAX_ROTATION_MINUTES = 24 * 60
MAX_QUOTE_FONT_SIZE = 56
MIN_QUOTE_FONT_SIZE = 12
QUOTE_FONT_STEP = 2
QUOTE_TEXT_REGION = (54, 38, WIDTH - 54, 258)
SETTINGS_FOOTER_REGION = (42, 416, WIDTH - 41, HEIGHT)


@dataclass(frozen=True, slots=True)
class TextStyle:
    bold: bool = False
    italic: bool = False
    highlighted: bool = False


@dataclass(frozen=True, slots=True)
class StyledRun:
    text: str
    style: TextStyle


@dataclass(frozen=True, slots=True)
class QuoteFonts:
    regular: ImageFont.ImageFont
    bold: ImageFont.ImageFont
    italic: ImageFont.ImageFont
    bold_italic: ImageFont.ImageFont

    def for_style(self, style: TextStyle) -> ImageFont.ImageFont:
        if style.bold and style.italic:
            return self.bold_italic
        if style.bold:
            return self.bold
        if style.italic:
            return self.italic
        return self.regular


@dataclass(frozen=True, slots=True)
class RenderedFrame:
    black: Image.Image
    red: Image.Image

    @classmethod
    def monochrome(cls, black: Image.Image) -> RenderedFrame:
        return cls(black, Image.new("1", black.size, 255))

    def preview(self) -> Image.Image:
        preview = Image.new("RGB", self.black.size, "white")
        black_mask = Image.eval(self.black, lambda value: 255 - value)
        red_mask = Image.eval(self.red, lambda value: 255 - value)
        preview.paste("black", mask=black_mask)
        preview.paste("red", mask=red_mask)
        return preview


CATALOG_PATH = Path(__file__).resolve().parents[1] / "quotes.yaml"
QUOTES = load_catalog(CATALOG_PATH)


@dataclass(frozen=True)
class StoredFrameState:
    quote_index: int = 0
    rotation_minutes: int = DEFAULT_ROTATION_MINUTES


@dataclass(frozen=True)
class FrameSnapshot:
    mode: Literal["quote", "settings"]
    quote: Quote
    quote_index: int
    quote_count: int
    rotation_minutes: int
    last_motion_at: datetime | None
    last_refresh_at: datetime | None


@dataclass(frozen=True)
class ActionResult:
    render: bool = False
    persist: bool = False
    message: str | None = None


class StateStore:
    """Atomically persist the small set of user-controlled frame state."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    def load(self, quote_count: int) -> StoredFrameState:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            state = StoredFrameState(
                quote_index=int(data["quote_index"]),
                rotation_minutes=int(data["rotation_minutes"]),
            )
            self._validate(state, quote_count)
            return state
        except FileNotFoundError:
            return StoredFrameState()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            print(
                f"warning: ignoring invalid frame state at {self.path}: {error}",
                file=sys.stderr,
            )
            return StoredFrameState()

    def save(self, state: StoredFrameState, quote_count: int) -> None:
        self._validate(state, quote_count)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.tmp")
            temporary.write_text(
                json.dumps(asdict(state), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)

    @staticmethod
    def _validate(state: StoredFrameState, quote_count: int) -> None:
        if not 0 <= state.quote_index < quote_count:
            raise ValueError("quote index is out of range")
        if not MIN_ROTATION_MINUTES <= state.rotation_minutes <= MAX_ROTATION_MINUTES:
            raise ValueError("rotation minutes are out of range")


class FrameController:
    """Own quote navigation, PIR gating, and the rotation setting."""

    def __init__(
        self,
        quotes: tuple[Quote, ...],
        stored: StoredFrameState,
        *,
        pir_activity_seconds: float = PIR_ACTIVITY_SECONDS,
    ) -> None:
        if not quotes:
            raise ValueError("at least one quote is required")
        if pir_activity_seconds <= 0:
            raise ValueError("PIR activity window must be greater than zero")
        StateStore._validate(stored, len(quotes))
        self._quotes = quotes
        self._lock = Lock()
        self._quote_index = stored.quote_index
        self._rotation_minutes = stored.rotation_minutes
        self._pir_activity_seconds = pir_activity_seconds
        self._mode: Literal["quote", "settings"] = "quote"
        self._last_motion_at: datetime | None = None
        self._last_motion_monotonic: float | None = None
        self._last_refresh_at: datetime | None = None
        self._next_rotation_at: float | None = None

    def observe_motion(self, now: float, wall_time: datetime) -> bool:
        """Record a PIR detection; return True when activity resumes."""
        with self._lock:
            was_active = self._is_active_locked(now)
            self._last_motion_at = wall_time
            self._last_motion_monotonic = now
            if not was_active and self._mode == "quote":
                self._next_rotation_at = now + self._rotation_seconds_locked()
            return not was_active

    def observe_active_sample(self, now: float, wall_time: datetime) -> bool:
        """Extend activity while PIR remains high without changing event time."""
        with self._lock:
            was_active = self._is_active_locked(now)
            self._last_motion_monotonic = now
            if not was_active:
                self._last_motion_at = wall_time
                if self._mode == "quote":
                    self._next_rotation_at = now + self._rotation_seconds_locked()
            return not was_active

    def tick(self, now: float) -> ActionResult:
        """Advance automatically when the PIR gate and rotation timer permit."""
        with self._lock:
            if self._mode != "quote":
                return ActionResult()
            if not self._is_active_locked(now):
                self._next_rotation_at = None
                return ActionResult()
            if self._next_rotation_at is None:
                self._next_rotation_at = now + self._rotation_seconds_locked()
                return ActionResult()
            if now < self._next_rotation_at:
                return ActionResult()
            self._quote_index = (self._quote_index + 1) % len(self._quotes)
            self._next_rotation_at = now + self._rotation_seconds_locked()
            return ActionResult(
                render=True,
                persist=True,
                message=f"automatic quote {self._quote_index + 1}/{len(self._quotes)}",
            )

    def rotate(self, direction: int, now: float) -> ActionResult:
        if direction not in (-1, 1):
            raise ValueError("direction must be -1 or 1")
        with self._lock:
            if self._mode == "settings":
                changed = self._adjust_setting_locked(direction)
                return ActionResult(
                    render=changed,
                    persist=changed,
                    message=self._settings_message_locked() if changed else None,
                )

            self._quote_index = (self._quote_index + direction) % len(self._quotes)
            if self._is_active_locked(now):
                self._next_rotation_at = now + self._rotation_seconds_locked()
            direction_label = "next" if direction > 0 else "previous"
            return ActionResult(
                render=True,
                persist=True,
                message=(
                    f"{direction_label} quote "
                    f"{self._quote_index + 1}/{len(self._quotes)}"
                ),
            )

    def short_press(self, now: float) -> ActionResult:
        """Toggle settings/quote mode; mode transitions always fully refresh."""
        with self._lock:
            if self._mode == "quote":
                self._mode = "settings"
                self._next_rotation_at = None
                message = "settings"
            else:
                self._mode = "quote"
                if self._is_active_locked(now):
                    self._next_rotation_at = now + self._rotation_seconds_locked()
                message = f"quote {self._quote_index + 1}/{len(self._quotes)}"
            return ActionResult(render=True, message=message)

    def prepare_snapshot(self, refresh_time: datetime) -> FrameSnapshot:
        """Capture one coherent frame and the refresh time it will display."""
        with self._lock:
            self._last_refresh_at = refresh_time
            return self._snapshot_locked()

    def snapshot(self) -> FrameSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def stored_state(self) -> StoredFrameState:
        with self._lock:
            return StoredFrameState(
                quote_index=self._quote_index,
                rotation_minutes=self._rotation_minutes,
            )

    def _snapshot_locked(self) -> FrameSnapshot:
        return FrameSnapshot(
            mode=self._mode,
            quote=self._quotes[self._quote_index],
            quote_index=self._quote_index,
            quote_count=len(self._quotes),
            rotation_minutes=self._rotation_minutes,
            last_motion_at=self._last_motion_at,
            last_refresh_at=self._last_refresh_at,
        )

    def _is_active_locked(self, now: float) -> bool:
        return (
            self._last_motion_monotonic is not None
            and now - self._last_motion_monotonic < self._pir_activity_seconds
        )

    def _rotation_seconds_locked(self) -> float:
        return self._rotation_minutes * 60.0

    def _adjust_setting_locked(self, direction: int) -> bool:
        value = min(
            MAX_ROTATION_MINUTES,
            max(MIN_ROTATION_MINUTES, self._rotation_minutes + direction),
        )
        if value == self._rotation_minutes:
            return False
        self._rotation_minutes = value
        return True

    def _settings_message_locked(self) -> str:
        return f"n={self._rotation_minutes} minutes"


class RefreshQueue:
    """Coalesce input received while the slow e-ink panel is busy."""

    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()
        self._pending = False

    def request(self) -> None:
        with self._lock:
            self._pending = True
            self._event.set()

    def take(self, timeout: float) -> bool | None:
        if not self._event.wait(timeout):
            return None
        with self._lock:
            if not self._pending:
                self._event.clear()
                return None
            self._pending = False
            self._event.clear()
            return True


_MARKUP_DELIMITERS = (
    ("**", "bold"),
    ("==", "highlighted"),
    ("_", "italic"),
)


def _append_styled_run(
    runs: list[StyledRun],
    text: str,
    style: TextStyle,
) -> None:
    if not text:
        return
    if runs and runs[-1].style == style:
        previous = runs[-1]
        runs[-1] = StyledRun(previous.text + text, style)
    else:
        runs.append(StyledRun(text, style))


def parse_styled_runs(text: str) -> list[StyledRun]:
    """Parse paired quote markup and retain every unmatched delimiter."""
    runs: list[StyledRun] = []
    active = {"bold": False, "italic": False, "highlighted": False}
    cursor = 0
    while cursor < len(text):
        matched = next(
            (
                (delimiter, attribute)
                for delimiter, attribute in _MARKUP_DELIMITERS
                if text.startswith(delimiter, cursor)
            ),
            None,
        )
        if matched is None:
            next_marker = min(
                (
                    position
                    for delimiter, _ in _MARKUP_DELIMITERS
                    if (position := text.find(delimiter, cursor + 1)) >= 0
                ),
                default=len(text),
            )
            style = TextStyle(**active)
            _append_styled_run(runs, text[cursor:next_marker], style)
            cursor = next_marker
            continue

        delimiter, attribute = matched
        if active[attribute]:
            active[attribute] = False
        elif text.find(delimiter, cursor + len(delimiter)) >= 0:
            active[attribute] = True
        else:
            _append_styled_run(runs, delimiter, TextStyle(**active))
        cursor += len(delimiter)
    return runs


def _styled_words(
    runs: list[StyledRun],
) -> list[tuple[TextStyle | None, list[StyledRun]]]:
    words: list[tuple[TextStyle | None, list[StyledRun]]] = []
    word: list[StyledRun] = []
    separator_style: TextStyle | None = None
    pending_separator_style: TextStyle | None = None
    for run in runs:
        for match in re.finditer(r"\s+|\S+", run.text):
            value = match.group(0)
            if value.isspace():
                if word:
                    words.append((separator_style, word))
                    word = []
                    separator_style = None
                pending_separator_style = run.style
                continue
            if not word:
                separator_style = pending_separator_style
                pending_separator_style = None
            _append_styled_run(word, value, run.style)
    if word:
        words.append((separator_style, word))
    return words


def wrap_styled_text(
    runs: list[StyledRun],
    fonts: QuoteFonts,
    max_width: int,
) -> list[list[StyledRun]]:
    lines: list[list[StyledRun]] = []
    line: list[StyledRun] = []
    line_width = 0.0
    for separator_style, word in _styled_words(runs):
        pieces = list(word)
        if line:
            space_style = separator_style or word[0].style
            pieces.insert(0, StyledRun(" ", space_style))
        width = styled_line_width(pieces, fonts)
        if line and line_width + width > max_width:
            lines.append(line)
            line = list(word)
            line_width = styled_line_width(word, fonts)
            continue
        for piece in pieces:
            _append_styled_run(line, piece.text, piece.style)
        line_width += width
    if line:
        lines.append(line)
    return lines


def styled_line_width(line: list[StyledRun], fonts: QuoteFonts) -> float:
    return sum(fonts.for_style(run.style).getlength(run.text) for run in line)


def styled_layout_fits(
    lines: list[list[StyledRun]],
    fonts: QuoteFonts,
    line_height: int,
    max_width: int,
    max_height: int,
) -> bool:
    total_height = len(lines) * line_height
    y = (max_height - total_height) // 2
    for line in lines:
        line_width = styled_line_width(line, fonts)
        x = (max_width - line_width) / 2
        for run in line:
            font = fonts.for_style(run.style)
            left, top, right, bottom = font.getbbox(run.text)
            if (
                x + left < 0
                or y + top < 0
                or x + right > max_width
                or y + bottom > max_height
            ):
                return False
            x += font.getlength(run.text)
        y += line_height
    return True


def _custom_style_font_paths(
    font_path: Path | None,
    *,
    bold: bool,
    italic: bool,
) -> list[Path]:
    if font_path is None:
        return []
    stem = re.sub(
        r"(?i)(?:[-_ ]?(?:regular|roman|book|medium|bolditalic|"
        r"boldoblique|bold|italic|oblique))$",
        "",
        font_path.stem,
    )
    stem = stem or font_path.stem
    if bold and italic:
        labels = (
            "BoldItalic",
            "Bold-Italic",
            "Bold Italic",
            "BoldOblique",
            "Bold-Oblique",
            "Bold Oblique",
        )
    elif bold:
        labels = ("Bold",)
    else:
        labels = ("Italic", "Oblique")
    candidates: list[Path] = []
    for label in labels:
        for separator in ("-", "_", " ", ""):
            candidate = font_path.with_name(
                f"{stem}{separator}{label}{font_path.suffix}"
            )
            if candidate.exists() and candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _load_custom_style_font(
    size: int,
    font_path: Path | None,
    *,
    bold: bool,
    italic: bool,
) -> ImageFont.ImageFont | None:
    for candidate in _custom_style_font_paths(
        font_path,
        bold=bold,
        italic=italic,
    ):
        try:
            return ImageFont.truetype(str(candidate), size)
        except OSError:
            continue
    return None


def _load_styled_font(
    size: int,
    font_path: Path | None,
    *,
    bold: bool,
    italic: bool,
) -> ImageFont.ImageFont:
    custom = _load_custom_style_font(
        size,
        font_path,
        bold=bold,
        italic=italic,
    )
    if custom is not None:
        return custom

    if bold and italic:
        style_name = "bold-italic"
        filename = "DejaVuSans-BoldOblique.ttf"
        mac_filename = "Arial Bold Italic.ttf"
    elif bold:
        style_name = "bold"
        filename = "DejaVuSans-Bold.ttf"
        mac_filename = "Arial Bold.ttf"
    else:
        style_name = "italic"
        filename = "DejaVuSans-Oblique.ttf"
        mac_filename = "Arial Italic.ttf"
    candidates = (
        filename,
        f"/usr/share/fonts/truetype/dejavu/{filename}",
        f"/System/Library/Fonts/Supplemental/{mac_filename}",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    custom_hint = f" beside {font_path}" if font_path is not None else " via --font"
    raise RuntimeError(
        f"Cannot load a genuine {style_name} quote font. Install the "
        f"DejaVu Sans {style_name} face or provide a matching family face"
        f"{custom_hint}."
    )


def load_bold_font(
    size: int,
    font_path: Path | None = None,
) -> ImageFont.ImageFont:
    return _load_styled_font(
        size,
        font_path,
        bold=True,
        italic=False,
    )


def load_italic_font(
    size: int,
    font_path: Path | None = None,
    *,
    bold: bool = False,
) -> ImageFont.ImageFont:
    return _load_styled_font(
        size,
        font_path,
        bold=bold,
        italic=True,
    )


def load_quote_fonts(
    size: int,
    font_path: Path | None,
) -> QuoteFonts:
    return QuoteFonts(
        regular=load_font(size, font_path=font_path),
        bold=load_bold_font(size, font_path),
        italic=load_italic_font(size, font_path),
        bold_italic=load_italic_font(size, font_path, bold=True),
    )


def text_width(font: ImageFont.ImageFont, text: str) -> int:
    return round(font.getlength(text))


def _require_bounds(
    bounds: tuple[float, float, float, float],
    region: tuple[int, int, int, int],
    label: str,
) -> None:
    left, top, right, bottom = bounds
    region_left, region_top, region_right, region_bottom = region
    if (
        left < region_left
        or top < region_top
        or right > region_right
        or bottom > region_bottom
    ):
        raise ValueError(f"{label} bounds {bounds} exceed region {region}")


def require_ink_within(
    layer: Image.Image,
    region: tuple[int, int, int, int],
    label: str,
) -> tuple[int, int, int, int] | None:
    bounds = ImageOps.invert(layer.convert("L")).getbbox()
    if bounds is not None:
        _require_bounds(bounds, region, label)
    return bounds


def _composite_ink(target: Image.Image, layer: Image.Image) -> None:
    target.paste(0, mask=ImageOps.invert(layer.convert("L")))


def draw_text_within(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    region: tuple[int, int, int, int],
    *,
    fill: int = 0,
) -> None:
    bounds = draw.textbbox(position, text, font=font)
    _require_bounds(bounds, region, text)
    draw.text(position, text, font=font, fill=fill)


def draw_right_aligned(
    draw: ImageDraw.ImageDraw,
    text: str,
    right: int,
    y: int,
    font: ImageFont.ImageFont,
    *,
    fill: int = 0,
    within: tuple[int, int, int, int] | None = None,
) -> None:
    position = (right - text_width(font, text), y)
    if within is not None:
        bounds = draw.textbbox(position, text, font=font)
        _require_bounds(bounds, within, text)
    draw.text(position, text, font=font, fill=fill)


def wrap_plain(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = word if not line else f"{line} {word}"
        if line and font.getlength(candidate) > max_width:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def format_frame_time(value: datetime | None) -> str:
    return "never" if value is None else value.astimezone().strftime("%Y-%m-%d %H:%M")


def quote_fonts_that_fit(
    quote: Quote,
    max_width: int,
    max_height: int,
    font_path: Path | None,
) -> tuple[QuoteFonts, list[list[StyledRun]], int]:
    runs = parse_styled_runs(quote.text)
    for size in range(
        MAX_QUOTE_FONT_SIZE,
        MIN_QUOTE_FONT_SIZE - 1,
        -QUOTE_FONT_STEP,
    ):
        fonts = load_quote_fonts(size, font_path)
        lines = wrap_styled_text(runs, fonts, max_width)
        line_height = round(size * 1.28)
        if lines and styled_layout_fits(
            lines,
            fonts,
            line_height,
            max_width,
            max_height,
        ):
            return fonts, lines, line_height
    raise ValueError(
        f"quote by {quote.author} does not fit at the minimum "
        f"{MIN_QUOTE_FONT_SIZE}-pixel font size"
    )


def render_quote_screen(
    snapshot: FrameSnapshot,
    *,
    font_path: Path | None = None,
) -> RenderedFrame:
    black = Image.new("1", (WIDTH, HEIGHT), 255)
    red = Image.new("1", (WIDTH, HEIGHT), 255)
    quote_black = Image.new("1", (WIDTH, HEIGHT), 255)
    quote_red = Image.new("1", (WIDTH, HEIGHT), 255)
    black_draw = ImageDraw.Draw(quote_black)
    red_draw = ImageDraw.Draw(quote_red)
    quote_left, quote_top, quote_right, quote_bottom = QUOTE_TEXT_REGION
    margin = quote_left
    right = quote_right

    fonts, lines, line_height = quote_fonts_that_fit(
        snapshot.quote,
        quote_right - quote_left,
        quote_bottom - quote_top,
        font_path,
    )
    quote_height = len(lines) * line_height
    y = quote_top + (quote_bottom - quote_top - quote_height) // 2
    for line in lines:
        width = styled_line_width(line, fonts)
        x = (WIDTH - width) / 2
        for run in line:
            font = fonts.for_style(run.style)
            draw = red_draw if run.style.highlighted else black_draw
            draw_text_within(
                draw,
                (round(x), y),
                run.text,
                font,
                QUOTE_TEXT_REGION,
            )
            x += font.getlength(run.text)
        y += line_height
    require_ink_within(quote_black, QUOTE_TEXT_REGION, "black quote")
    require_ink_within(quote_red, QUOTE_TEXT_REGION, "red quote")
    _composite_ink(black, quote_black)
    _composite_ink(red, quote_red)
    draw = ImageDraw.Draw(black)

    author_font = load_font(26, bold=True, font_path=font_path)
    source_font = load_italic_font(21, font_path)
    note_font = load_font(18, font_path=font_path)
    footer_font = load_font(14, font_path=font_path)

    metadata_y = 278
    draw_right_aligned(
        draw,
        f"— {snapshot.quote.author}",
        right,
        metadata_y,
        author_font,
    )
    metadata_y += 38
    for line in wrap_plain(snapshot.quote.source, source_font, 590):
        draw_right_aligned(draw, line, right, metadata_y, source_font)
        metadata_y += 29

    if snapshot.quote.notes:
        rule_y = max(metadata_y + 5, 360)
        draw.line((WIDTH - 480, rule_y, right, rule_y), fill=0, width=1)
        note_y = rule_y + 12
        for line in wrap_plain(snapshot.quote.notes, note_font, 480):
            draw_right_aligned(draw, line, right, note_y, note_font)
            note_y += 24

    footer_top = 422
    draw.line((margin, footer_top, right, footer_top), fill=0, width=1)
    footer_y = footer_top + 20
    motion_text = f"LAST MOTION  {format_frame_time(snapshot.last_motion_at)}"
    refresh_text = f"LAST REFRESH  {format_frame_time(snapshot.last_refresh_at)}"
    draw.text((margin, footer_y), motion_text, font=footer_font, fill=0)
    draw_right_aligned(
        draw,
        refresh_text,
        right,
        footer_y,
        footer_font,
    )
    return RenderedFrame(black, red)


def render_settings_screen(
    snapshot: FrameSnapshot,
    *,
    font_path: Path | None = None,
) -> Image.Image:
    image = Image.new("1", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(image)
    header_font = load_font(27, bold=True, font_path=font_path)
    row_label_font = load_font(20, bold=True, font_path=font_path)
    value_font = load_font(68, bold=True, font_path=font_path)
    unit_font = load_font(18, font_path=font_path)
    footer_font = load_font(15, font_path=font_path)

    draw.rectangle((0, 0, WIDTH - 1, 58), fill=0)
    draw.text((28, 14), "EPIGRAPH SETTINGS", font=header_font, fill=255)
    draw.text((610, 18), "TURN TO ADJUST", font=unit_font, fill=255)

    top = 126
    bottom = 342
    draw.rectangle((72, top, WIDTH - 72, bottom), fill=0)
    draw.text((104, top + 34), "MINUTES PER QUOTE", font=row_label_font, fill=255)
    value_text = str(snapshot.rotation_minutes)
    draw.text((WIDTH - 250, top + 58), value_text, font=value_font, fill=255)
    footer = Image.new("1", (WIDTH, HEIGHT), 255)
    footer_draw = ImageDraw.Draw(footer)
    footer_left, footer_top, footer_right, _ = SETTINGS_FOOTER_REGION
    footer_draw.line(
        (footer_left, footer_top, footer_right - 1, footer_top),
        fill=0,
        width=1,
    )
    draw_text_within(
        footer_draw,
        (footer_left, 436),
        "CLICK: RETURN TO QUOTE",
        footer_font,
        SETTINGS_FOOTER_REGION,
    )
    refresh_text = f"REFRESH  {format_frame_time(snapshot.last_refresh_at)}"
    draw_right_aligned(
        footer_draw,
        refresh_text,
        footer_right - 1,
        458,
        footer_font,
        within=SETTINGS_FOOTER_REGION,
    )
    require_ink_within(footer, SETTINGS_FOOTER_REGION, "settings footer")
    _composite_ink(image, footer)
    return image


def render_frame(
    snapshot: FrameSnapshot,
    *,
    font_path: Path | None = None,
) -> RenderedFrame:
    if snapshot.mode == "settings":
        black = render_settings_screen(snapshot, font_path=font_path)
        return RenderedFrame.monochrome(black)
    return render_quote_screen(snapshot, font_path=font_path)


def default_state_path() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home) if state_home else Path.home() / ".local/state"
    return base / "epigraph/frame.json"


def timestamp() -> str:
    return time.strftime("%H:%M:%S")


def _raise_keyboard_interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def register_shutdown_signal(
    register: Callable[[int, Callable[[int, object], None]], object] = signal,
) -> None:
    register(SIGTERM, _raise_keyboard_interrupt)


def run_hardware(
    *,
    state_path: Path,
    waveshare_lib: Path | None,
    font_path: Path | None,
) -> None:
    register_shutdown_signal()
    try:
        from gpiozero import Button, MotionSensor, RotaryEncoder
    except ImportError as error:
        raise RuntimeError(
            "gpiozero is required on the Raspberry Pi: sudo apt install python3-gpiozero"
        ) from error

    store = StateStore(state_path)
    controller = FrameController(QUOTES, store.load(len(QUOTES)))
    queue = RefreshQueue()
    devices: list[object] = []
    display: WaveshareDisplay | None = None

    def apply(result: ActionResult) -> None:
        if result.persist:
            store.save(controller.stored_state(), len(QUOTES))
        if result.message:
            print(f"[{timestamp()}] {result.message}", flush=True)
        if result.render:
            queue.request()

    def rotate(direction: int) -> None:
        apply(controller.rotate(direction, time.monotonic()))

    def button_pressed() -> None:
        apply(controller.short_press(time.monotonic()))

    def motion_detected() -> None:
        resumed = controller.observe_motion(time.monotonic(), datetime.now(UTC))
        suffix = "; rotation timer started" if resumed else ""
        print(f"[{timestamp()}] motion detected{suffix}", flush=True)

    try:
        encoder = RotaryEncoder(ENCODER_A_GPIO, ENCODER_B_GPIO, max_steps=0)
        button = Button(ENCODER_SWITCH_GPIO, pull_up=True, bounce_time=0.05)
        pir = MotionSensor(PIR_GPIO, queue_len=1, sample_rate=10)
        devices.extend((encoder, button, pir))

        encoder.when_rotated_clockwise = lambda: rotate(1)
        encoder.when_rotated_counter_clockwise = lambda: rotate(-1)
        button.when_pressed = button_pressed
        pir.when_motion = motion_detected

        store.save(controller.stored_state(), len(QUOTES))
        display = WaveshareDisplay(waveshare_lib)
        first_snapshot = controller.prepare_snapshot(datetime.now(UTC))
        print("Rendering initial quote (full refresh)...", flush=True)
        frame = render_frame(first_snapshot, font_path=font_path)
        display.show(frame.black, frame.red)
        print(
            f"[{timestamp()}] ready; automatic rotation waits for motion",
            flush=True,
        )

        while True:
            now = time.monotonic()
            if pir.motion_detected and controller.observe_active_sample(
                now,
                datetime.now(UTC),
            ):
                print(
                    f"[{timestamp()}] motion active; rotation timer started",
                    flush=True,
                )
            apply(controller.tick(now))

            if queue.take(0.1) is None:
                continue
            snapshot = controller.prepare_snapshot(datetime.now(UTC))
            frame = render_frame(snapshot, font_path=font_path)
            print(f"[{timestamp()}] full display refresh", flush=True)
            display.show(frame.black, frame.red)
    except KeyboardInterrupt:
        print("\nStopping Epigraph Frame...", flush=True)
    finally:
        for device in devices:
            close = getattr(device, "close", None)
            if close is not None:
                close()
        if display is not None:
            display.close()


def render_previews(directory: Path, font_path: Path | None) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 8, 2, 12, 34, tzinfo=UTC)
    controller = FrameController(QUOTES, StoredFrameState())
    controller.observe_motion(0.0, datetime(2026, 8, 2, 12, 30, tzinfo=UTC))
    outputs: list[Path] = []

    for index in range(len(QUOTES)):
        if index:
            controller.rotate(1, 1.0)
        snapshot = controller.prepare_snapshot(now)
        output = directory / f"quote-{index + 1}.png"
        render_quote_screen(snapshot, font_path=font_path).preview().save(output)
        outputs.append(output)

    controller.short_press(2.0)
    settings_snapshot = controller.prepare_snapshot(now)
    output = directory / "settings.png"
    render_frame(settings_snapshot, font_path=font_path).preview().save(output)
    outputs.append(output)
    return outputs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Display and rotate the local Epigraph quote catalog."
    )
    parser.add_argument(
        "--preview-dir",
        type=Path,
        help="render quote and settings PNGs without accessing frame hardware",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=default_state_path(),
        help="persistent state path (default: ~/.local/state/epigraph/frame.json)",
    )
    parser.add_argument(
        "--waveshare-lib",
        type=Path,
        help="path containing the waveshare_epd package (auto-detects ~/e-Paper/.../lib)",
    )
    parser.add_argument(
        "--font",
        type=Path,
        help="optional TrueType/OpenType font path",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.preview_dir is not None:
        outputs = render_previews(args.preview_dir.expanduser().resolve(), args.font)
        for output in outputs:
            print(f"Rendered {WIDTH}x{HEIGHT} preview: {output}")
        return 0

    run_hardware(
        state_path=args.state_file.expanduser(),
        waveshare_lib=args.waveshare_lib,
        font_path=args.font,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
