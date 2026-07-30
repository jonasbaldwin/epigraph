# Epigraph

Epigraph manages a personal catalog of styled quotations, previews unpublished changes in Epigraph Studio, publishes reviewed content to GitHub, and presents the Published Catalog through browser and Epigraph Frame e-ink displays.

## Language

**Quote**:
A quotation and its optional attribution, source, explanation, enabled state, and inline styling. Its ID is stable and is never reused for different content.
_Avoid_: Quote entry, record, row

**Catalog**:
An ordered collection of Quotes. Catalog order determines automatic rotation and previous/next navigation.
_Avoid_: Feed, database, quote list

**Draft Catalog**:
The local working-copy Catalog, including changes not yet committed to the public content repository.
_Avoid_: Local database, unpublished feed

**Published Catalog**:
The Catalog committed to the content repository's `main` branch and eligible for retrieval by the e-ink display.
_Avoid_: Production database, remote feed

**Publish**:
The explicit transition that validates the Draft Catalog, commits only its canonical YAML file, and pushes that commit to `main`.
_Avoid_: Upload, sync, deploy
