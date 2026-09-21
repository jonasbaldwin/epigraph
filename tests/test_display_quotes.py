import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from signal import SIGTERM
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_hardware import HEIGHT, WIDTH
from display_quotes import (
    MIN_QUOTE_FONT_SIZE,
    QUOTE_TEXT_REGION,
    QUOTES,
    FrameController,
    FrameSnapshot,
    Quote,
    QuoteFonts,
    RefreshQueue,
    StateStore,
    StoredFrameState,
    StyledRun,
    TextStyle,
    load_bold_font,
    load_italic_font,
    load_quote_fonts,
    parse_styled_runs,
    quote_fonts_that_fit,
    register_shutdown_signal,
    render_frame,
    render_quote_screen,
    require_ink_within,
    wrap_styled_text,
)


class QuoteContentTests(unittest.TestCase):
    def test_catalog_contains_existing_and_enabled_source_quotes(self) -> None:
        self.assertGreaterEqual(len(QUOTES), 56)
        self.assertEqual(len({quote.text for quote in QUOTES}), len(QUOTES))
        self.assertIn("being **wrong**", QUOTES[0].text)
        self.assertIn("being ==**right**==", QUOTES[0].text)
        self.assertEqual(
            QUOTES[0].source,
            "Being Wrong: Adventures in the Margin of Error",
        )
        self.assertEqual(
            QUOTES[0].notes,
            "Summarized in How Minds Change by David McRaney",
        )
        self.assertIn("there is no certainty", QUOTES[1].text)
        self.assertEqual(QUOTES[1].source, "Essais de Critique Générale")
        self.assertTrue(
            any(
                quote.author == "debateherofficial" and quote.source == "Instagram"
                for quote in QUOTES
            )
        )
        self.assertTrue(
            all(
                not quote.text.startswith(("“", '"'))
                and not quote.text.endswith(("”", '"'))
                for quote in QUOTES
            )
        )


class MarkupTests(unittest.TestCase):
    def test_independent_styles_are_parsed_without_matched_markers(self) -> None:
        runs = parse_styled_runs("**bold** _italic_ ==highlight==")

        self.assertEqual("".join(run.text for run in runs), "bold italic highlight")
        self.assertEqual(
            [(run.text, run.style) for run in runs],
            [
                ("bold", TextStyle(bold=True)),
                (" ", TextStyle()),
                ("italic", TextStyle(italic=True)),
                (" ", TextStyle()),
                ("highlight", TextStyle(highlighted=True)),
            ],
        )

    def test_styles_stack_in_any_nesting_order(self) -> None:
        self.assertEqual(
            parse_styled_runs("**_wrong_**"),
            [StyledRun("wrong", TextStyle(bold=True, italic=True))],
        )
        nested = parse_styled_runs("==_**all**_==")
        self.assertEqual(len(nested), 1)
        self.assertEqual(
            nested[0].style,
            TextStyle(bold=True, italic=True, highlighted=True),
        )

    def test_unmatched_delimiters_remain_visible(self) -> None:
        text = "keep **bold and _italic and ==highlight"
        runs = parse_styled_runs(text)

        self.assertEqual("".join(run.text for run in runs), text)
        self.assertTrue(all(run.style == TextStyle() for run in runs))

    def test_style_boundary_inside_word_uses_each_font_without_spaces(self) -> None:
        class FixedFont:
            size = 20

            def __init__(self, width: int) -> None:
                self.width = width

            def getlength(self, text: str) -> int:
                return len(text) * self.width

        fonts = QuoteFonts(
            FixedFont(1),
            FixedFont(2),
            FixedFont(3),
            FixedFont(4),
        )
        lines = wrap_styled_text(
            parse_styled_runs("a**b**_c_**_d_** next"),
            fonts,
            12,
        )

        self.assertEqual(
            ["".join(run.text for run in line) for line in lines],
            ["abcd", "next"],
        )


class FontSelectionTests(unittest.TestCase):
    class Face:
        def __init__(self, name: str) -> None:
            self.name = name

    def test_custom_family_uses_distinct_style_siblings(self) -> None:
        with TemporaryDirectory() as directory:
            family = Path(directory)
            regular = family / "Family-Regular.ttf"
            for name in (
                "Family-Regular.ttf",
                "Family-Bold.ttf",
                "Family-Italic.ttf",
                "Family-BoldItalic.ttf",
            ):
                (family / name).write_bytes(b"face")

            with (
                patch(
                    "display_quotes.load_font",
                    return_value=self.Face("Family-Regular"),
                ),
                patch(
                    "display_quotes.ImageFont.truetype",
                    side_effect=lambda path, _size: self.Face(Path(path).stem),
                ),
            ):
                fonts = load_quote_fonts(32, regular)

        self.assertEqual(fonts.regular.name, "Family-Regular")
        self.assertEqual(fonts.bold.name, "Family-Bold")
        self.assertEqual(fonts.italic.name, "Family-Italic")
        self.assertEqual(fonts.bold_italic.name, "Family-BoldItalic")

    def test_missing_custom_variants_use_real_style_fallbacks(self) -> None:
        def fake_load_font(
            _size: int,
            *,
            bold: bool = False,
            font_path: Path | None = None,
        ) -> "FontSelectionTests.Face":
            if font_path is not None:
                return self.Face("Custom-Regular")
            return self.Face("Fallback-Bold" if bold else "Fallback-Regular")

        with TemporaryDirectory() as directory:
            regular = Path(directory) / "Custom-Regular.ttf"
            regular.write_bytes(b"face")
            with (
                patch("display_quotes.load_font", side_effect=fake_load_font),
                patch(
                    "display_quotes.ImageFont.truetype",
                    side_effect=lambda path, _size, **_kwargs: self.Face(
                        Path(path).stem
                    ),
                ),
            ):
                fonts = load_quote_fonts(32, regular)

        self.assertEqual(fonts.regular.name, "Custom-Regular")
        self.assertEqual(fonts.bold.name, "DejaVuSans-Bold")
        self.assertEqual(fonts.italic.name, "DejaVuSans-Oblique")
        self.assertEqual(fonts.bold_italic.name, "DejaVuSans-BoldOblique")

    def test_styled_face_failure_is_explicit_without_generic_fallback(self) -> None:
        loaders = (
            ("bold", lambda: load_bold_font(32)),
            ("italic", lambda: load_italic_font(32)),
            ("bold-italic", lambda: load_italic_font(32, bold=True)),
        )
        with (
            patch(
                "display_quotes.ImageFont.truetype",
                side_effect=OSError("missing face"),
            ) as truetype,
            patch(
                "display_quotes.ImageFont.load_default",
                side_effect=AssertionError("generic fallback used"),
            ),
        ):
            for style_name, loader in loaders:
                with (
                    self.subTest(style=style_name),
                    self.assertRaisesRegex(RuntimeError, style_name),
                ):
                    loader()

        attempted = [str(call.args[0]) for call in truetype.call_args_list]
        self.assertFalse(any(path.endswith("Font.ttc") for path in attempted))


class FrameControllerTests(unittest.TestCase):
    def make_controller(
        self,
        *,
        rotation_minutes: int = 1,
        pir_activity_seconds: float = 120,
    ) -> FrameController:
        return FrameController(
            QUOTES,
            StoredFrameState(
                quote_index=0,
                rotation_minutes=rotation_minutes,
            ),
            pir_activity_seconds=pir_activity_seconds,
        )

    def test_motion_starts_full_rotation_interval(self) -> None:
        controller = self.make_controller()
        wall_time = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)

        self.assertTrue(controller.observe_motion(10.0, wall_time))
        self.assertFalse(controller.tick(69.9).render)
        result = controller.tick(70.0)

        self.assertTrue(result.render)
        self.assertEqual(controller.snapshot().quote_index, 1)
        self.assertEqual(controller.snapshot().last_motion_at, wall_time)

    def test_latest_motion_event_updates_footer_time_while_activity_is_live(
        self,
    ) -> None:
        controller = self.make_controller()
        first = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
        latest = datetime(2026, 8, 2, 12, 1, tzinfo=UTC)

        controller.observe_motion(0.0, first)
        controller.observe_active_sample(
            30.0,
            datetime(2026, 8, 2, 12, 0, 30, tzinfo=UTC),
        )
        controller.observe_motion(60.0, latest)

        self.assertEqual(controller.snapshot().last_motion_at, latest)

    def test_pir_inactivity_pauses_and_new_motion_restarts_interval(self) -> None:
        controller = self.make_controller(pir_activity_seconds=5)
        controller.observe_motion(0.0, datetime(2026, 8, 2, 12, 0, tzinfo=UTC))

        self.assertFalse(controller.tick(6.0).render)
        self.assertTrue(
            controller.observe_motion(
                10.0,
                datetime(2026, 8, 2, 12, 10, tzinfo=UTC),
            )
        )
        self.assertFalse(controller.tick(69.9).render)
        self.assertFalse(controller.tick(70.0).render)

    def test_manual_navigation_wraps_without_motion(self) -> None:
        controller = self.make_controller()

        previous = controller.rotate(-1, 0.0)
        self.assertTrue(previous.render)
        self.assertEqual(controller.snapshot().quote_index, len(QUOTES) - 1)

        next_result = controller.rotate(1, 1.0)
        self.assertTrue(next_result.render)
        self.assertEqual(controller.snapshot().quote_index, 0)

    def test_settings_rotation_edits_rotation_interval(self) -> None:
        controller = self.make_controller(rotation_minutes=2)

        entered = controller.short_press(0.0)
        changed = controller.rotate(1, 1.0)
        exited = controller.short_press(2.0)

        snapshot = controller.snapshot()
        self.assertTrue(entered.render)
        self.assertTrue(changed.persist)
        self.assertTrue(exited.render)
        self.assertEqual(snapshot.mode, "quote")
        self.assertEqual(snapshot.rotation_minutes, 3)

    def test_prepare_snapshot_records_displayed_refresh_time(self) -> None:
        controller = self.make_controller()
        refresh_time = datetime(2026, 8, 2, 12, 34, tzinfo=UTC)

        snapshot = controller.prepare_snapshot(refresh_time)

        self.assertEqual(snapshot.last_refresh_at, refresh_time)


class StateStoreTests(unittest.TestCase):
    def test_state_round_trips_atomically(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "nested/frame.json"
            store = StateStore(path)
            state = StoredFrameState(
                quote_index=1,
                rotation_minutes=2,
            )

            store.save(state, len(QUOTES))

            self.assertEqual(store.load(len(QUOTES)), state)
            self.assertFalse(path.with_name(".frame.json.tmp").exists())

    def test_legacy_refresh_interval_is_ignored(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "frame.json"
            path.write_text(
                '{"quote_index": 1, "rotation_minutes": 2, "full_refresh_every": 5}',
                encoding="utf-8",
            )

            self.assertEqual(
                StateStore(path).load(len(QUOTES)),
                StoredFrameState(quote_index=1, rotation_minutes=2),
            )

    def test_invalid_state_falls_back_to_defaults(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "frame.json"
            path.write_text('{"quote_index": 99}', encoding="utf-8")

            state = StateStore(path).load(len(QUOTES))

            self.assertEqual(state, StoredFrameState())


class AdaptiveTypographyTests(unittest.TestCase):
    def test_longer_quote_uses_smaller_measured_font(self) -> None:
        short = Quote(
            id="short",
            text="A concise thought.",
            author="Author",
            source="Source",
        )
        long = Quote(
            id="long",
            text=" ".join(
                [
                    "A longer thought must remain readable while it wraps naturally"
                    for _ in range(8)
                ]
            ),
            author="Author",
            source="Source",
        )

        short_fonts, _, _ = quote_fonts_that_fit(short, 692, 220, None)
        long_fonts, _, _ = quote_fonts_that_fit(long, 692, 220, None)

        self.assertGreater(short_fonts.regular.size, long_fonts.regular.size)
        self.assertGreaterEqual(long_fonts.regular.size, MIN_QUOTE_FONT_SIZE)

    def test_unbreakable_quote_that_cannot_fit_is_rejected(self) -> None:
        quote = Quote(id="unbreakable", text="W" * 1000, author="Author", source="")

        with self.assertRaisesRegex(ValueError, "minimum"):
            quote_fonts_that_fit(quote, 692, 220, None)


class ShutdownSignalTests(unittest.TestCase):
    def test_sigterm_is_registered_as_clean_keyboard_interrupt(self) -> None:
        registrations: list[tuple[int, object]] = []
        register_shutdown_signal(
            lambda signum, handler: registrations.append((signum, handler))
        )

        self.assertEqual(registrations[0][0], SIGTERM)
        handler = registrations[0][1]
        with self.assertRaises(KeyboardInterrupt):
            handler(SIGTERM, None)


class RenderingTests(unittest.TestCase):
    @staticmethod
    def quote_snapshot(text: str) -> FrameSnapshot:
        return FrameSnapshot(
            mode="quote",
            quote=Quote(
                id="render-test",
                text=text,
                author="Author",
                source="",
            ),
            quote_index=0,
            quote_count=1,
            rotation_minutes=2,
            last_motion_at=None,
            last_refresh_at=None,
        )

    def test_full_canvas_bounds_reject_vertical_overflow(self) -> None:
        for y in (QUOTE_TEXT_REGION[1] - 1, QUOTE_TEXT_REGION[3]):
            with self.subTest(y=y):
                layer = Image.new("1", (WIDTH, HEIGHT), 255)
                layer.putpixel((WIDTH // 2, y), 0)

                with self.assertRaisesRegex(ValueError, "exceed region"):
                    require_ink_within(layer, QUOTE_TEXT_REGION, "quote")

    def test_highlighted_quote_glyphs_render_only_on_red_plane(self) -> None:
        highlighted = render_quote_screen(self.quote_snapshot("==highlight=="))
        plain = render_quote_screen(self.quote_snapshot("plain"))
        quote_band = (0, 38, WIDTH, 258)

        self.assertEqual(
            highlighted.black.crop(quote_band).getextrema(),
            (255, 255),
        )
        self.assertEqual(
            highlighted.red.crop(quote_band).getextrema(),
            (0, 255),
        )
        self.assertEqual(plain.black.crop(quote_band).getextrema(), (0, 255))
        self.assertEqual(plain.red.crop(quote_band).getextrema(), (255, 255))

    def test_preview_combines_white_black_and_red(self) -> None:
        preview = render_quote_screen(self.quote_snapshot("black ==red==")).preview()
        colors = {color for _, color in preview.getcolors()}

        self.assertTrue({(255, 255, 255), (0, 0, 0), (255, 0, 0)} <= colors)

    def test_catalog_and_settings_pass_checked_panel_regions(self) -> None:
        controller = FrameController(QUOTES, StoredFrameState())
        frames = []

        for index in range(len(QUOTES)):
            if index:
                controller.rotate(1, float(index))
            snapshot = controller.prepare_snapshot(
                datetime(2026, 8, 2, 12, index, tzinfo=UTC),
            )
            frame = render_frame(snapshot)
            frames.append(frame)

        controller.short_press(float(len(QUOTES)))
        settings = controller.prepare_snapshot(
            datetime(2026, 8, 2, 13, 0, tzinfo=UTC),
        )
        frames.append(render_frame(settings))

        planes = [plane for frame in frames for plane in (frame.black, frame.red)]
        self.assertTrue(all(plane.size == (WIDTH, HEIGHT) for plane in planes))
        self.assertTrue(all(plane.mode == "1" for plane in planes))
        self.assertEqual(frames[-1].red.getextrema(), (255, 255))
        self.assertNotEqual(frames[0].black.tobytes(), frames[1].black.tobytes())
        self.assertNotEqual(frames[1].black.tobytes(), frames[-1].black.tobytes())


class RefreshQueueTests(unittest.TestCase):
    def test_requests_coalesce(self) -> None:
        queue = RefreshQueue()

        queue.request()
        queue.request()
        queue.request()

        self.assertTrue(queue.take(0.0))
        self.assertIsNone(queue.take(0.0))


if __name__ == "__main__":
    unittest.main()
