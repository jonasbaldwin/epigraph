import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_hardware import (
    HEIGHT,
    WIDTH,
    DiagnosticSnapshot,
    DiagnosticState,
    WaveshareDisplay,
    render_screen,
)


class DiagnosticStateTests(unittest.TestCase):
    def test_motion_stays_on_until_timeout_after_latest_active_sample(self) -> None:
        state = DiagnosticState(pir_timeout_seconds=5)

        self.assertTrue(state.observe_motion(10.0))
        self.assertFalse(state.observe_motion(13.0))
        self.assertFalse(state.expire_motion(17.9))
        self.assertTrue(state.snapshot().pir_active)
        self.assertTrue(state.expire_motion(18.0))
        self.assertFalse(state.snapshot().pir_active)

    def test_each_encoder_event_updates_latest_label_and_its_count(self) -> None:
        state = DiagnosticState()

        self.assertEqual(state.record_clockwise(), 1)
        self.assertEqual(state.record_clockwise(), 2)
        self.assertEqual(state.record_counter_clockwise(), 1)
        self.assertEqual(state.record_button(), 1)

        snapshot = state.snapshot()
        self.assertEqual(snapshot.encoder_event, "BUTTON PRESSED")
        self.assertEqual(snapshot.clockwise_count, 2)
        self.assertEqual(snapshot.counter_clockwise_count, 1)
        self.assertEqual(snapshot.button_count, 1)

    def test_timeout_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            DiagnosticState(pir_timeout_seconds=0)


class DiagnosticRendererTests(unittest.TestCase):
    def test_render_produces_panel_sized_monochrome_image(self) -> None:
        snapshot = DiagnosticSnapshot(
            pir_active=False,
            encoder_event="COUNTER-CLOCKWISE",
            clockwise_count=1,
            counter_clockwise_count=2,
            button_count=3,
            revision=6,
        )

        image = render_screen(snapshot)

        self.assertEqual(image.size, (WIDTH, HEIGHT))
        self.assertEqual(image.mode, "1")

    def test_on_and_off_states_render_differently(self) -> None:
        off = DiagnosticSnapshot(False, "WAITING FOR INPUT", 0, 0, 0, 0)
        on = DiagnosticSnapshot(True, "WAITING FOR INPUT", 0, 0, 0, 1)

        self.assertNotEqual(render_screen(off).tobytes(), render_screen(on).tobytes())


class FakeEpd:
    width = WIDTH
    height = HEIGHT

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def init(self) -> int:
        self.calls.append(("init",))
        return 0

    def getbuffer(self, image: Image.Image) -> tuple[int, int]:
        data = image.tobytes()
        return len(data), sum(data)

    def display(
        self,
        black: tuple[int, int],
        red: tuple[int, int],
    ) -> None:
        self.calls.append(("display", black, red))

    def sleep(self) -> None:
        self.calls.append(("sleep",))


class WaveshareDisplayTests(unittest.TestCase):
    def make_display(self) -> tuple[WaveshareDisplay, FakeEpd, list[bool]]:
        fake_epd = FakeEpd()
        cleanup_calls: list[bool] = []
        display = WaveshareDisplay.__new__(WaveshareDisplay)
        display._epd = fake_epd
        display._module = SimpleNamespace(
            epdconfig=SimpleNamespace(
                module_exit=lambda cleanup=False: cleanup_calls.append(cleanup)
            )
        )
        display._awake = False
        return display, fake_epd, cleanup_calls

    def test_every_update_uses_standard_full_refresh_and_sleeps(self) -> None:
        display, fake_epd, _ = self.make_display()
        initial = render_screen(
            DiagnosticSnapshot(False, "WAITING FOR INPUT", 0, 0, 0, 0)
        )
        changed = render_screen(DiagnosticSnapshot(True, "MOTION DETECTED", 1, 0, 0, 1))

        display.show(initial)
        display.show(changed)

        self.assertEqual(
            [call[0] for call in fake_epd.calls],
            ["init", "display", "sleep", "init", "display", "sleep"],
        )
        blank_red = fake_epd.getbuffer(Image.new("1", (WIDTH, HEIGHT), 255))
        display_calls = [call for call in fake_epd.calls if call[0] == "display"]
        self.assertTrue(all(call[2] == blank_red for call in display_calls))
        self.assertFalse(display._awake)

    def test_update_submits_caller_provided_red_plane(self) -> None:
        display, fake_epd, _ = self.make_display()
        black = Image.new("1", (WIDTH, HEIGHT), 255)
        red = Image.new("1", (WIDTH, HEIGHT), 255)
        black.putpixel((10, 10), 0)
        red.putpixel((20, 20), 0)
        red.putpixel((21, 20), 0)

        display.show(black, red)

        display_call = next(call for call in fake_epd.calls if call[0] == "display")
        self.assertEqual(display_call[1], fake_epd.getbuffer(black))
        self.assertEqual(display_call[2], fake_epd.getbuffer(red))
        self.assertNotEqual(display_call[1], display_call[2])

    def test_close_requests_gpio_cleanup(self) -> None:
        display, _, cleanup_calls = self.make_display()

        display.close()

        self.assertEqual(cleanup_calls, [True])


if __name__ == "__main__":
    unittest.main()
