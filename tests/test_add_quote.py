import argparse
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from add_quote import (
    AddQuoteError,
    QuoteDraft,
    append_quote,
    prompt_for_quote,
    run,
)
from catalog import load_catalog
from update_pi import SshTarget, UpdateError


CATALOG = """\
version: 1
quotes:
  - id: existing-thought
    quote: An existing thought.
    attribution: Existing Author
    enabled: true
"""


class QuotePromptTests(unittest.TestCase):
    def test_prompts_for_quote_author_source_and_notes_in_order(self) -> None:
        answers = iter(["  A useful thought.  ", "  An Author  ", "", "  Context  "])
        prompts: list[str] = []

        def read_value(prompt: str) -> str:
            prompts.append(prompt)
            return next(answers)

        draft = prompt_for_quote(read_value)

        self.assertEqual(
            draft,
            QuoteDraft("A useful thought.", "An Author", None, "Context"),
        )
        self.assertEqual(
            prompts,
            ["Quote: ", "Author: ", "Source (optional): ", "Notes (optional): "],
        )

    def test_blank_required_value_is_rejected(self) -> None:
        with self.assertRaisesRegex(AddQuoteError, "Quote is required"):
            prompt_for_quote(lambda prompt: "")


class CatalogAppendTests(unittest.TestCase):
    def make_catalog(self, directory: str, content: str = CATALOG) -> Path:
        path = Path(directory) / "quotes.yaml"
        path.write_text(content, encoding="utf-8")
        return path

    def test_appends_valid_yaml_without_rewriting_existing_content(self) -> None:
        with TemporaryDirectory() as directory:
            path = self.make_catalog(directory)
            original = path.read_text(encoding="utf-8")

            quote_id = append_quote(
                path,
                QuoteDraft("A new thought.", "New Author"),
            )

            content = path.read_text(encoding="utf-8")
            document = yaml.safe_load(content)
            quotes = load_catalog(path)

        self.assertEqual(quote_id, "new-author-a-new-thought")
        self.assertTrue(content.startswith(original))
        self.assertEqual(quotes[-1].text, "A new thought.")
        self.assertNotIn("source", document["quotes"][-1])
        self.assertNotIn("explanation", document["quotes"][-1])
        self.assertTrue(document["quotes"][-1]["enabled"])

    def test_suffixes_an_id_collision(self) -> None:
        with TemporaryDirectory() as directory:
            path = self.make_catalog(directory)
            draft = QuoteDraft("Same thought.", "Same Author")

            first_id = append_quote(path, draft)
            second_id = append_quote(path, draft)
            quotes = load_catalog(path)

        self.assertEqual(first_id, "same-author-same-thought")
        self.assertEqual(second_id, "same-author-same-thought-2")
        self.assertEqual(len({quote.id for quote in quotes}), 3)

    def test_invalid_catalog_is_not_changed(self) -> None:
        invalid = "version: 1\nquotes:\n  - unknown: field\n"
        with TemporaryDirectory() as directory:
            path = self.make_catalog(directory, invalid)

            with self.assertRaises(AddQuoteError):
                append_quote(path, QuoteDraft("A thought.", "An Author"))

            self.assertEqual(path.read_text(encoding="utf-8"), invalid)


class AddAndUpdateFlowTests(unittest.TestCase):
    def make_repository(self, directory: str) -> Path:
        root = Path(directory)
        (root / "scripts").mkdir()
        (root / "systemd").mkdir()
        (root / "quotes.yaml").write_text(CATALOG, encoding="utf-8")
        for filename in ("catalog.py", "check_hardware.py", "display_quotes.py"):
            (root / "scripts" / filename).write_text("stub\n", encoding="utf-8")
        (root / "systemd" / "epigraph-frame.service").write_text(
            "stub\n", encoding="utf-8"
        )
        return root

    def test_adds_quote_then_invokes_existing_pi_updater(self) -> None:
        with TemporaryDirectory() as directory:
            root = self.make_repository(directory)
            answers = iter(["A deployed thought.", "Deploy Author", "Book", "Page 3"])
            calls: list[tuple[SshTarget, tuple[str, ...]]] = []

            quote_id = run(
                argparse.Namespace(user="pi", host="Epigraph-Frame.local", port=2222),
                reader=lambda prompt: next(answers),
                repository_root=root,
                ssh_check=lambda: None,
                updater=lambda target, files: calls.append(
                    (target, tuple(path.name for path in files))
                ),
            )
            quotes = load_catalog(root / "quotes.yaml")

        self.assertEqual(quote_id, "deploy-author-a-deployed-thought")
        self.assertEqual(quotes[-1].source, "Book")
        self.assertEqual(quotes[-1].notes, "Page 3")
        self.assertEqual(calls[0][0], SshTarget("pi", "epigraph-frame.local", 2222))
        self.assertEqual(
            calls[0][1],
            (
                "catalog.py",
                "check_hardware.py",
                "display_quotes.py",
                "quotes.yaml",
                "epigraph-frame.service",
            ),
        )

    def test_failed_pi_update_retains_local_quote_for_retry(self) -> None:
        with TemporaryDirectory() as directory:
            root = self.make_repository(directory)
            answers = iter(["A retained thought.", "Retry Author", "", ""])

            def fail_update(target: SshTarget, files: tuple[Path, ...]) -> None:
                raise UpdateError("connection failed")

            with self.assertRaisesRegex(
                AddQuoteError,
                "saved locally.*Retry with scripts/update_pi.py",
            ):
                run(
                    argparse.Namespace(user="pi", host="frame.local", port=22),
                    reader=lambda prompt: next(answers),
                    repository_root=root,
                    ssh_check=lambda: None,
                    updater=fail_update,
                )

            quotes = load_catalog(root / "quotes.yaml")

        self.assertEqual(quotes[-1].text, "A retained thought.")


if __name__ == "__main__":
    unittest.main()
