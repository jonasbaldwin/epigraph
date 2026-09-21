#!/usr/bin/env python3
"""Add a quote to the canonical catalog and install it on the Epigraph Frame."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable, Sequence

import yaml

from catalog import CatalogError, load_catalog
from update_pi import (
    CATALOG_FILE,
    RUNTIME_FILES,
    SERVICE_FILE,
    SshTarget,
    UpdateError,
    ensure_openssh,
    prompt_for,
    update_pi,
    validate_host,
    validate_port,
    validate_username,
)


class AddQuoteError(RuntimeError):
    """A safe, operator-facing quote addition failure."""


@dataclass(frozen=True)
class QuoteDraft:
    text: str
    author: str
    source: str | None = None
    notes: str | None = None


InputReader = Callable[[str], str]
PiUpdater = Callable[[SshTarget, Sequence[Path]], None]


def prompt_required(label: str, reader: InputReader = input) -> str:
    """Prompt for a non-empty value."""
    try:
        value = reader(f"{label}: ").strip()
    except EOFError as error:
        raise AddQuoteError(f"{label} is required") from error
    if not value:
        raise AddQuoteError(f"{label} is required")
    return value


def prompt_optional(label: str, reader: InputReader = input) -> str | None:
    """Prompt for a value that may be omitted."""
    try:
        return reader(f"{label} (optional): ").strip() or None
    except EOFError as error:
        raise AddQuoteError(f"could not read {label.lower()}") from error


def prompt_for_quote(reader: InputReader = input) -> QuoteDraft:
    """Collect the four operator-facing quote fields in order."""
    return QuoteDraft(
        text=prompt_required("Quote", reader),
        author=prompt_required("Author", reader),
        source=prompt_optional("Source", reader),
        notes=prompt_optional("Notes", reader),
    )


def slugify(value: str) -> str:
    """Return a lowercase ASCII identifier fragment."""
    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", "-", without_marks.casefold()).strip("-")


def unique_quote_id(draft: QuoteDraft, existing_ids: set[str]) -> str:
    """Derive a readable, deterministic ID and avoid catalog collisions."""
    maximum_length = 80
    base = slugify(f"{draft.author} {draft.text}")[:maximum_length].rstrip("-")
    if not base:
        base = "quote"
    if base not in existing_ids:
        return base

    sequence = 2
    while True:
        suffix = f"-{sequence}"
        candidate = f"{base[: maximum_length - len(suffix)].rstrip('-')}{suffix}"
        if candidate not in existing_ids:
            return candidate
        sequence += 1


def _quote_yaml(quote_id: str, draft: QuoteDraft) -> str:
    item: dict[str, str | bool] = {
        "id": quote_id,
        "quote": draft.text,
        "attribution": draft.author,
    }
    if draft.source is not None:
        item["source"] = draft.source
    if draft.notes is not None:
        item["explanation"] = draft.notes
    item["enabled"] = True

    dumped = yaml.safe_dump(
        [item],
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=4096,
    )
    return "".join(f"  {line}\n" for line in dumped.splitlines())


def append_quote(catalog_path: Path, draft: QuoteDraft) -> str:
    """Validate and atomically append a Quote while preserving existing YAML."""
    try:
        original = catalog_path.read_text(encoding="utf-8")
        original_mode = stat.S_IMODE(catalog_path.stat().st_mode)
    except OSError as error:
        raise AddQuoteError(f"cannot read catalog at {catalog_path}: {error}") from error

    try:
        load_catalog(catalog_path)
        document = yaml.safe_load(original)
    except (CatalogError, yaml.YAMLError) as error:
        raise AddQuoteError(str(error)) from error

    items = document["quotes"]
    existing_ids = {item["id"].strip() for item in items}
    quote_id = unique_quote_id(draft, existing_ids)
    separator = "" if original.endswith("\n") else "\n"
    candidate = f"{original}{separator}{_quote_yaml(quote_id, draft)}"

    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=catalog_path.parent,
            prefix=f".{catalog_path.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(candidate)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.chmod(original_mode)
        load_catalog(temporary_path)
        os.replace(temporary_path, catalog_path)
        temporary_path = None
    except (OSError, CatalogError) as error:
        raise AddQuoteError(f"could not save quote: {error}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return quote_id


def deployment_files(repository_root: Path) -> tuple[Path, ...]:
    script_directory = repository_root / "scripts"
    return tuple(script_directory / filename for filename in RUNTIME_FILES) + (
        repository_root / CATALOG_FILE,
        repository_root / "systemd" / SERVICE_FILE,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add a quote to quotes.yaml and install the updated catalog on the Pi."
    )
    parser.add_argument("--user", help="SSH username (prompted when omitted)")
    parser.add_argument(
        "--host", help="Pi hostname or IP address (prompted when omitted)"
    )
    parser.add_argument(
        "--port",
        type=validate_port,
        default=22,
        help="SSH port (default: 22)",
    )
    return parser.parse_args(argv)


def run(
    args: argparse.Namespace,
    *,
    reader: InputReader = input,
    repository_root: Path | None = None,
    ssh_check: Callable[[], None] = ensure_openssh,
    updater: PiUpdater = update_pi,
) -> str:
    root = Path(__file__).resolve().parent.parent if repository_root is None else repository_root
    draft = prompt_for_quote(reader)
    user = prompt_for(args.user, "SSH username", validate_username, reader=reader)
    host = prompt_for(args.host, "Pi address", validate_host, reader=reader)
    target = SshTarget(user=user, host=host, port=args.port)

    ssh_check()
    quote_id = append_quote(root / CATALOG_FILE, draft)
    print(f"Added {quote_id} to {CATALOG_FILE}.", flush=True)

    try:
        updater(target, deployment_files(root))
    except UpdateError as error:
        raise AddQuoteError(
            f"Quote {quote_id} was saved locally, but the Pi update failed: {error}. "
            "Retry with scripts/update_pi.py; do not add the quote again."
        ) from error

    print(
        f"Updated {target.ssh_destination}. The new quote is now running on "
        "Epigraph Frame.",
        flush=True,
    )
    return quote_id


def main(argv: list[str] | None = None) -> int:
    run(parse_args(argv))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AddQuoteError, UpdateError, argparse.ArgumentTypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
