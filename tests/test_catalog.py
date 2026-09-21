import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from catalog import CatalogError, load_catalog


class CatalogLoaderTests(unittest.TestCase):
    def load(self, content: str):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "quotes.yaml"
            path.write_text(content, encoding="utf-8")
            return load_catalog(path)

    def test_loads_enabled_quotes_and_skips_disabled_quotes(self) -> None:
        quotes = self.load(
            """\
version: 1
quotes:
  - id: enabled
    quote: A useful thought.
    attribution: Author
  - id: disabled
    quote: Not currently displayed.
    enabled: false
"""
        )

        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].id, "enabled")
        self.assertEqual(quotes[0].author, "Author")
        self.assertEqual(quotes[0].source, "")

    def test_rejects_duplicate_ids(self) -> None:
        with self.assertRaisesRegex(CatalogError, "duplicates 'same'"):
            self.load(
                """\
version: 1
quotes:
  - id: same
    quote: First.
  - id: same
    quote: Second.
"""
            )

    def test_canonical_catalog_contains_all_imported_quotes(self) -> None:
        path = Path(__file__).resolve().parents[1] / "quotes.yaml"
        quotes = load_catalog(path)

        self.assertGreaterEqual(len(quotes), 56)
        self.assertEqual(len({quote.id for quote in quotes}), len(quotes))
        self.assertEqual(quotes[0].author, "Kathryn Schulz")
        self.assertEqual(quotes[1].author, "Charles Renouvier")
        self.assertTrue(any(quote.author == "debateherofficial" for quote in quotes))


if __name__ == "__main__":
    unittest.main()
