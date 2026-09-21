#!/usr/bin/env python3
"""Load and validate the canonical Epigraph YAML catalog."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CATALOG_VERSION = 1
QUOTE_FIELDS = {
    "id",
    "quote",
    "attribution",
    "source",
    "explanation",
    "enabled",
}


class CatalogError(ValueError):
    """The catalog does not satisfy the version-one schema."""


@dataclass(frozen=True)
class Quote:
    id: str
    text: str
    author: str
    source: str
    notes: str | None = None


def _required_text(item: dict[str, Any], field: str, location: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{location}.{field} must be a non-empty string")
    return value.strip()


def _optional_text(item: dict[str, Any], field: str, location: str) -> str | None:
    value = item.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CatalogError(f"{location}.{field} must be a string when present")
    return value.strip() or None


def load_catalog(path: Path) -> tuple[Quote, ...]:
    """Return enabled quotes from a validated version-one YAML catalog."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise CatalogError(f"cannot read catalog at {path}: {error}") from error
    except yaml.YAMLError as error:
        raise CatalogError(f"invalid YAML in catalog at {path}: {error}") from error

    if not isinstance(document, dict):
        raise CatalogError("catalog must be a mapping")
    if document.get("version") != CATALOG_VERSION:
        raise CatalogError(f"catalog.version must be {CATALOG_VERSION}")

    items = document.get("quotes")
    if not isinstance(items, list):
        raise CatalogError("catalog.quotes must be a list")

    quotes: list[Quote] = []
    seen_ids: set[str] = set()
    for index, value in enumerate(items):
        location = f"catalog.quotes[{index}]"
        if not isinstance(value, dict):
            raise CatalogError(f"{location} must be a mapping")

        unknown_fields = set(value) - QUOTE_FIELDS
        if unknown_fields:
            names = ", ".join(sorted(str(field) for field in unknown_fields))
            raise CatalogError(f"{location} has unknown fields: {names}")

        quote_id = _required_text(value, "id", location)
        if quote_id in seen_ids:
            raise CatalogError(f"{location}.id duplicates {quote_id!r}")
        seen_ids.add(quote_id)

        enabled = value.get("enabled", True)
        if not isinstance(enabled, bool):
            raise CatalogError(f"{location}.enabled must be a boolean")

        text = _required_text(value, "quote", location)
        author = _optional_text(value, "attribution", location) or "Unknown"
        source = _optional_text(value, "source", location) or ""
        notes = _optional_text(value, "explanation", location)
        if enabled:
            quotes.append(
                Quote(
                    id=quote_id,
                    text=text,
                    author=author,
                    source=source,
                    notes=notes,
                )
            )

    if not quotes:
        raise CatalogError("catalog must contain at least one enabled quote")
    return tuple(quotes)
