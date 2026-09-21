#!/usr/bin/env python3
"""Interactive wiring check for the Epigraph Frame hardware."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock

from PIL import Image, ImageDraw, ImageFont

WIDTH = 800
HEIGHT = 480
ENCODER_A_GPIO = 5
ENCODER_B_GPIO = 6
ENCODER_SWITCH_GPIO = 13
PIR_GPIO = 16
DEFAULT_PIR_TIMEOUT_SECONDS = 5 * 60


@dataclass(frozen=True)
class DiagnosticSnapshot:
    pir_active: bool
    encoder_event: str
    clockwise_count: int
    counter_clockwise_count: int
    button_count: int
    revision: int


class DiagnosticState:
    """Thread-safe input state with a retriggerable PIR inactivity window."""

    def __init__(
        self, pir_timeout_seconds: float = DEFAULT_PIR_TIMEOUT_SECONDS
    ) -> None:
        if pir_timeout_seconds <= 0:
            raise ValueError("PIR timeout must be greater than zero")
        self._pir_timeout_seconds = pir_timeout_seconds
        self._lock = Lock()
        self._pir_active = False
        self._last_motion_at: float | None = None
        self._encoder_event = "WAITING FOR INPUT"
        self._clockwise_count = 0
        self._counter_clockwise_count = 0
        self._button_count = 0
        self._revision = 0

    def observe_motion(self, now: float) -> bool:
        """Record an active PIR sample; return True only when the UI changes."""
        with self._lock:
            self._last_motion_at = now
            if self._pir_active:
                return False
            self._pir_active = True
            self._revision += 1
            return True

    def expire_motion(self, now: float) -> bool:
        """Turn PIR state off after a complete inactive interval."""
        with self._lock:
            if not self._pir_active or self._last_motion_at is None:
                return False
            if now - self._last_motion_at < self._pir_timeout_seconds:
                return False
            self._pir_active = False
            self._revision += 1
            return True

    def record_clockwise(self) -> int:
        return self._record_encoder("CLOCKWISE", "clockwise")

    def record_counter_clockwise(self) -> int:
        return self._record_encoder("COUNTER-CLOCKWISE", "counter_clockwise")

    def record_button(self) -> int:
        return self._record_encoder("BUTTON PRESSED", "button")

    def _record_encoder(self, label: str, event: str) -> int:
        with self._lock:
            self._encoder_event = label
            if event == "clockwise":
                self._clockwise_count += 1
                count = self._clockwise_count
            elif event == "counter_clockwise":
                self._counter_clockwise_count += 1
                count = self._counter_clockwise_count
            else:
                self._button_count += 1
                count = self._button_count
            self._revision += 1
            return count

    def snapshot(self) -> DiagnosticSnapshot:
        with self._lock:
            return DiagnosticSnapshot(
                pir_active=self._pir_active,
                encoder_event=self._encoder_event,
                clockwise_count=self._clockwise_count,
                counter_clockwise_count=self._counter_clockwise_count,
                button_count=self._button_count,
                revision=self._revision,
            )


def load_font(
    size: int,
    *,
    bold: bool = False,
    font_path: Path | None = None,
) -> ImageFont.ImageFont:
    candidates: list[tuple[str, int]] = []
    if font_path is not None:
        candidates.append((str(font_path), 0))
    candidates.extend(
        [
            ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", 0),
            (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                if bold
                else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                0,
            ),
            ("/System/Library/Fonts/Avenir Next.ttc", 0 if bold else 7),
            ("/System/Library/Fonts/Avenir Next Condensed.ttc", 0 if bold else 7),
            (
                str(Path.home() / "e-Paper/RaspberryPi_JetsonNano/python/pic/Font.ttc"),
                0,
            ),
        ]
    )
    for candidate, index in candidates:
        try:
            return ImageFont.truetype(candidate, size, index=index)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_centered(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    *,
    fill: int,
) -> None:
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    draw.text(
        (xy[0] - width // 2, xy[1] - height // 2 - bounds[1]),
        text,
        font=font,
        fill=fill,
    )


def event_font(text: str, font_path: Path | None) -> ImageFont.ImageFont:
    for size in (50, 44, 38, 32):
        font = load_font(size, bold=True, font_path=font_path)
        try:
            if font.getlength(text) <= 400:
                return font
        except AttributeError:
            return font
    return load_font(32, bold=True, font_path=font_path)


def render_screen(
    snapshot: DiagnosticSnapshot,
    *,
    font_path: Path | None = None,
) -> Image.Image:
    """Render the 800×480 black-and-white diagnostic panel."""
    image = Image.new("1", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(image)

    header_height = 58
    footer_top = 438
    split_x = 344
    draw.rectangle((0, 0, WIDTH - 1, header_height), fill=0)
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), outline=0, width=3)
    draw.line((split_x, header_height, split_x, footer_top), fill=0, width=3)
    draw.line((0, footer_top, WIDTH, footer_top), fill=0, width=2)

    title_font = load_font(27, bold=True, font_path=font_path)
    label_font = load_font(19, bold=True, font_path=font_path)
    small_font = load_font(16, font_path=font_path)
    metric_font = load_font(22, bold=True, font_path=font_path)
    state_font = load_font(94, bold=True, font_path=font_path)

    draw.text((24, 13), "EPIGRAPH", font=title_font, fill=255)
    header_right = "WIRING CHECK  /  LIVE"
    header_bounds = draw.textbbox((0, 0), header_right, font=label_font)
    draw.text(
        (WIDTH - 24 - (header_bounds[2] - header_bounds[0]), 18),
        header_right,
        font=label_font,
        fill=255,
    )

    draw.text((26, 82), "PIR SENSOR", font=label_font, fill=0)
    draw.text((26, 111), "OUT  ·  BCM 16", font=small_font, fill=0)
    draw.line((26, 143, split_x - 26, 143), fill=0, width=2)

    state_text = "ON" if snapshot.pir_active else "OFF"
    state_box = (35, 176, split_x - 35, 322)
    if snapshot.pir_active:
        draw.rectangle(state_box, fill=0)
        draw_centered(
            draw,
            ((state_box[0] + state_box[2]) // 2, 245),
            state_text,
            state_font,
            fill=255,
        )
    else:
        draw.rectangle(state_box, outline=0, width=5)
        draw_centered(
            draw,
            ((state_box[0] + state_box[2]) // 2, 245),
            state_text,
            state_font,
            fill=0,
        )

    pir_note = "MOTION SEEN" if snapshot.pir_active else "NO MOTION FOR 5 MIN"
    draw_centered(draw, (split_x // 2, 358), pir_note, label_font, fill=0)

    right_left = split_x + 28
    right_right = WIDTH - 26
    draw.text((right_left, 82), "ROTARY ENCODER", font=label_font, fill=0)
    draw.text((right_left, 111), "A 5  ·  B 6  ·  SWITCH 13", font=small_font, fill=0)
    draw.line((right_left, 143, right_right, 143), fill=0, width=2)
    draw.text((right_left, 164), "LATEST INPUT", font=small_font, fill=0)
    draw_centered(
        draw,
        ((right_left + right_right) // 2, 236),
        snapshot.encoder_event,
        event_font(snapshot.encoder_event, font_path),
        fill=0,
    )

    count_top = 298
    count_width = (right_right - right_left) // 3
    counts = (
        ("CW", snapshot.clockwise_count),
        ("CCW", snapshot.counter_clockwise_count),
        ("PRESS", snapshot.button_count),
    )
    for index, (label, count) in enumerate(counts):
        left = right_left + index * count_width
        center = left + count_width // 2
        if index:
            draw.line((left, count_top, left, 408), fill=0, width=1)
        draw_centered(draw, (center, 325), label, small_font, fill=0)
        draw_centered(draw, (center, 373), str(count), metric_font, fill=0)

    draw.text(
        (20, 450),
        "Turn both ways · press once · move in front of PIR",
        font=small_font,
        fill=0,
    )
    footer_text = "CTRL+C TO EXIT"
    footer_bounds = draw.textbbox((0, 0), footer_text, font=small_font)
    draw.text(
        (WIDTH - 20 - (footer_bounds[2] - footer_bounds[0]), 450),
        footer_text,
        font=small_font,
        fill=0,
    )
    return image


class WaveshareDisplay:
    """Confirmed V3 panel adapter using Waveshare's V2-compatible driver."""

    def __init__(self, waveshare_lib: Path | None = None) -> None:
        if waveshare_lib is not None:
            sys.path.insert(0, str(waveshare_lib.expanduser().resolve()))
        else:
            default_lib = Path.home() / "e-Paper/RaspberryPi_JetsonNano/python/lib"
            if default_lib.exists():
                sys.path.insert(0, str(default_lib))
        try:
            from waveshare_epd import epd7in5b_V2
        except ImportError as error:
            raise RuntimeError(
                "Cannot import Waveshare's epd7in5b_V2 driver. Clone the official "
                "e-Paper repository to ~/e-Paper or pass --waveshare-lib."
            ) from error

        self._module = epd7in5b_V2
        self._epd = epd7in5b_V2.EPD()
        if (self._epd.width, self._epd.height) != (WIDTH, HEIGHT):
            raise RuntimeError(
                f"Expected an {WIDTH}x{HEIGHT} V2/V3 panel, got "
                f"{self._epd.width}x{self._epd.height}."
            )
        self._awake = False

    def show(
        self,
        black: Image.Image,
        red: Image.Image | None = None,
    ) -> None:
        """Perform one standard full update across both panel planes."""
        if red is None:
            red = Image.new("1", (WIDTH, HEIGHT), 255)
        if self._epd.init() != 0:
            raise RuntimeError("Waveshare display initialization failed")
        self._awake = True
        try:
            self._epd.display(
                self._epd.getbuffer(black),
                self._epd.getbuffer(red),
            )
        finally:
            self.sleep()

    def sleep(self) -> None:
        if not self._awake:
            return
        self._awake = False
        self._epd.sleep()

    def close(self) -> None:
        try:
            self.sleep()
        finally:
            self._module.epdconfig.module_exit(cleanup=True)


def timestamp() -> str:
    return time.strftime("%H:%M:%S")


def run_hardware(
    *,
    pir_timeout_seconds: float,
    waveshare_lib: Path | None,
    font_path: Path | None,
) -> None:
    try:
        from gpiozero import Button, MotionSensor, RotaryEncoder
    except ImportError as error:
        raise RuntimeError(
            "gpiozero is required on the Raspberry Pi: sudo apt install python3-gpiozero"
        ) from error

    state = DiagnosticState(pir_timeout_seconds)
    changed = Event()
    devices: list[object] = []
    display: WaveshareDisplay | None = None

    def record_encoder(action: Callable[[], int], label: str) -> None:
        count = action()
        print(f"[{timestamp()}] ENCODER {label} (count {count})", flush=True)
        changed.set()

    def motion_started() -> None:
        if state.observe_motion(time.monotonic()):
            print(f"[{timestamp()}] PIR ON", flush=True)
            changed.set()

    try:
        encoder = RotaryEncoder(ENCODER_A_GPIO, ENCODER_B_GPIO, max_steps=0)
        button = Button(ENCODER_SWITCH_GPIO, pull_up=True, bounce_time=0.05)
        pir = MotionSensor(PIR_GPIO, queue_len=1, sample_rate=10)
        devices.extend((encoder, button, pir))

        encoder.when_rotated_clockwise = lambda: record_encoder(
            state.record_clockwise, "CLOCKWISE"
        )
        encoder.when_rotated_counter_clockwise = lambda: record_encoder(
            state.record_counter_clockwise, "COUNTER-CLOCKWISE"
        )
        button.when_pressed = lambda: record_encoder(
            state.record_button, "BUTTON PRESSED"
        )
        pir.when_motion = motion_started

        display = WaveshareDisplay(waveshare_lib)
        print("Rendering initial wiring-check screen (full refresh)...", flush=True)
        display.show(render_screen(state.snapshot(), font_path=font_path))
        print(
            f"[{timestamp()}] READY — PIR OFF; move, turn, and press. "
            "Press Ctrl+C to exit.",
            flush=True,
        )

        while True:
            now = time.monotonic()
            if pir.motion_detected:
                motion_started()
            elif state.expire_motion(now):
                print(f"[{timestamp()}] PIR OFF", flush=True)
                changed.set()

            if not changed.wait(0.1):
                continue
            changed.clear()
            snapshot = state.snapshot()
            display.show(render_screen(snapshot, font_path=font_path))
            if state.snapshot().revision != snapshot.revision:
                changed.set()
    except KeyboardInterrupt:
        print("\nStopping hardware check...", flush=True)
    finally:
        for device in devices:
            close = getattr(device, "close", None)
            if close is not None:
                close()
        if display is not None:
            display.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check the Epigraph PIR, rotary encoder, button, and e-ink wiring."
    )
    parser.add_argument(
        "--preview",
        type=Path,
        metavar="PNG",
        help="render an 800x480 sample PNG without accessing GPIO or the display",
    )
    parser.add_argument(
        "--pir-timeout-seconds",
        type=float,
        default=DEFAULT_PIR_TIMEOUT_SECONDS,
        help="seconds without an active PIR signal before OFF (default: 300)",
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
    if args.preview is not None:
        preview = DiagnosticSnapshot(
            pir_active=True,
            encoder_event="CLOCKWISE",
            clockwise_count=2,
            counter_clockwise_count=1,
            button_count=1,
            revision=4,
        )
        output = args.preview.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        render_screen(preview, font_path=args.font).save(output)
        print(f"Rendered {WIDTH}x{HEIGHT} preview: {output}")
        return 0

    run_hardware(
        pir_timeout_seconds=args.pir_timeout_seconds,
        waveshare_lib=args.waveshare_lib,
        font_path=args.font,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
