# Epigraph implementation plan

**Status:** Recommended architecture, ready for a hardware proof of concept  
**Researched:** 2026-07-30

**Project:** Epigraph  
**Application repository:** `jonasbaldwin/epigraph`  
**Surfaces:** Epigraph Frame (e-ink device), Epigraph Studio (local web editor), and Epigraph Catalog (published quote content)  
**Planned content repository:** `jonasbaldwin/epigraph-catalog`

## Decision

Use a **Raspberry Pi Zero 2 W** if one is available. An original **Pi Zero W** is also adequate. Do not use a non-wireless Pi Zero unless adding networking is acceptable.

A Pico W can drive the panel, but it is the wrong default for this version of the project. The Pi Zero can fetch a public GitHub content file, parse styled text, lay it out with real fonts, cache content, run at boot, log failures, and update over SSH using ordinary Python/Linux tooling. The Pico W is attractive only if low power is the primary requirement and quote rendering moves to a remote build service.

The purchased display is a **Waveshare 7.5-inch (B) raw red/black/white panel**, not a complete HAT. The indexed Amazon listing identifies ASIN `B076BVFHDV` as the raw panel, and the listing image matches Waveshare's raw panel. Waveshare explicitly says the raw panel has no driver PCB. It must connect through a compatible high-voltage e-paper driver board; it must not connect directly to a Pi or Pico GPIO header.

Buy the [Waveshare Universal e-Paper Driver HAT](https://www.waveshare.com/e-paper-driver-hat.htm) unless a compatible driver board was included separately. It is listed by Waveshare as supporting the 7.5-inch e-Paper (B) raw panel.

## Hardware facts and one required check

The current 7.5-inch (B) panel is 800×480, three-color, SPI, and takes about 26 seconds for a full color refresh. It retains an image without power. Waveshare's manual documents three panel revisions:

- V1: 640×384; use the V1 driver.
- V2: 800×480; use the V2 driver.
- V3: 800×480 and compatible with the V2 driver.

**Before implementing the renderer, inspect the label on the back of the panel and the code printed on its flex cable.** The Amazon ASIN has existed across product revisions, so the URL alone is not enough to select the driver. Photograph both labels and keep them with the project records. The software setting will be either `epd7in5b` for V1 or `epd7in5b_V2` for V2/V3, matching Waveshare's official Python driver names.

Use a full color refresh for quote changes. Some current Waveshare material mentions black/white partial refresh, while its Universal Driver HAT compatibility table marks the 7.5-inch (B) as not supporting partial refresh. More importantly, changing red text requires a full three-color update. The design therefore does not depend on partial refresh.

Waveshare recommends at least 180 seconds between refreshes and requires the panel to be put to sleep or powered off when it is not refreshing. A default quote interval of **60 minutes** is conservative. Buttons may request an immediate full refresh.

## Parts

Required:

- Existing Waveshare 7.5-inch e-Paper (B) raw panel.
- Waveshare Universal e-Paper Driver HAT.
- Raspberry Pi Zero 2 W with a soldered 40-pin male header; an original Zero W is acceptable.
- Reliable 5 V micro-USB supply. The official 12.5 W Zero supply is ample.
- microSD card.
- Two normally-open momentary push buttons.
- Hookup wire and a frame/enclosure that does not press on the glass or bend the flex cable sharply.

Useful but optional:

- JST/GH cable supplied with the driver HAT if the HAT cannot be stacked in the final enclosure.
- One small status LED and resistor. This helps because each full refresh takes about 26 seconds, but it is not required for the initial build.

## Recommended wiring: Pi Zero

### Display

The simplest arrangement is to insert the raw panel flex cable into the Universal Driver HAT and stack the HAT on the Pi Zero's 40-pin header. Open the connector latch before inserting the flex cable, follow the board's contact-orientation diagram, lock the latch, and never insert or remove it while powered.

For a cabled installation, use the following Waveshare mapping. `BCM` numbers are the GPIO identifiers used in software.

| Driver HAT | Pi signal | BCM | Physical pin |
|---|---|---:|---:|
| VCC | 3.3 V | — | 1 |
| GND | Ground | — | 6 or another ground pin |
| DIN | SPI0 MOSI | 10 | 19 |
| CLK | SPI0 SCLK | 11 | 23 |
| CS | SPI0 CE0 | 8 | 24 |
| DC | Data/command | 25 | 22 |
| RST | Reset | 17 | 11 |
| BUSY | Busy input | 24 | 18 |
| PWR | Driver power control | 18 | 12 |

The current Universal Driver HAT exposes `PWR`. If an older board has only eight signal pins, omit `PWR` and use the matching older Waveshare configuration. Do not power the HAT separately while it is also powered through the Pi header.

### Buttons

Wire each normally-open button between a GPIO and ground. Use the Pi's internal pull-ups, so pressed is active-low.

| Action | BCM | Physical pin | Other button terminal |
|---|---:|---:|---:|
| Previous | 5 | 29 | GND, physical pin 30 |
| Next | 6 | 31 | GND, physical pin 34 |

Use 75 ms software debounce. A button press changes the quote, performs one full refresh, and resets the automatic rotation timer. Presses received while the panel is busy are coalesced to the latest requested destination rather than starting unbounded refreshes.

## Pico W alternative

A Pico W is electrically capable. One valid SPI0 mapping to the Universal Driver HAT is:

| Driver HAT | Pico W GPIO | Pico physical pin |
|---|---:|---:|
| VCC | 3V3 OUT | 36 |
| GND | GND | 38 |
| DIN | GP19 / SPI0 TX | 25 |
| CLK | GP18 / SPI0 SCK | 24 |
| CS | GP17 | 22 |
| DC | GP20 | 26 |
| RST | GP21 | 27 |
| BUSY | GP22 | 29 |
| PWR | GP16 | 21 |
| Previous button | GP14 to GND | 19 |
| Next button | GP15 to GND | 20 |

Do not choose this architecture for local rich-text rendering. An 800×480 one-bit plane is 48,000 bytes; black and red planes consume 96,000 bytes before markup, fonts, layout, Wi-Fi, TLS, and runtime overhead. The Pico W has 264 kB SRAM and 2 MB flash. A practical Pico design would have a remote process pre-render each quote into two raw bitplanes; the Pico would only download/cache a manifest and stream the selected planes to the display. That adds a hosted renderer and a custom firmware update path, so it is more complex overall.

## Quote catalog and publishing

Use a **separate public GitHub repository** for published quote content. Keep application releases in the application repository and quote commits in the content repository so changing a quote never deploys software.

The content repository contains one canonical file at its root, `quotes.yaml`:

```yaml
version: 1
quotes:
  - id: meditations-change
    quote: |-
      _The universe is change_; our life is what our thoughts make it.
      ==Attend to them.==
    attribution: Marcus Aurelius
    source: Meditations
    explanation: >-
      A reminder to notice the frame I bring to events.
    enabled: true
```

Each quote supports these exact fields:

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Stable immutable slug; never reused for a different quote. |
| `quote` | yes | Quote body with constrained inline markup. YAML block text is allowed. |
| `attribution` | no | Speaker, author, translator, or other attribution. |
| `source` | no | Book, essay, speech, chapter, page, etc. |
| `explanation` | no | Brief note shown in a smaller lower section. |
| `enabled` | no | Boolean; omitted means `true`. |

YAML list order is display order. Previous/next wrap at the ends. Version one is deliberately sequential; shuffle can be added later only if wanted.

### Draft and published catalogs

The local web editor operates on a normal local clone of the content repository:

- **Draft Catalog**: the working-tree `quotes.yaml`, including uncommitted edits.
- **Published Catalog**: `quotes.yaml` on the remote repository's `main` branch.
- **Publish**: validate the Draft Catalog, commit it, and push that commit to `main`.

CRUD operations write the Draft Catalog atomically but do not commit it. The web interface shows whether unpublished changes exist and provides explicit Preview, Publish, and Discard Draft actions. Discard Draft requires confirmation.

The e-ink display never reads the Draft Catalog. It polls the Published Catalog through GitHub's repository contents endpoint:

```text
GET /repos/{owner}/{repository}/contents/quotes.yaml?ref=main
Accept: application/vnd.github.raw+json
```

```text
Edit quotes locally in the web application
          ↓
Review the Draft Catalog and e-ink preview
          ↓
Choose Publish
          ↓
Web app validates, commits, and pushes quotes.yaml
          ↓
Pi discovers the Published Catalog on its next poll
```

The quote repository and its full commit history are public. Do not commit private explanations, credentials, or material that must later be erased; deleting text from the current file does not remove it from Git history.

### SQLite decision

Do not commit an SQLite database as the shared catalog. GitHub would accept a small database file, but size is not the limiting issue:

- SQLite is binary, so quote changes have poor diffs and merge conflicts cannot be resolved meaningfully.
- Git must transfer and version a database image rather than a small text change.
- A live SQLite database may also depend on a rollback journal or WAL file. Copying only the main file during a transaction can produce an inconsistent copy; SQLite recommends its backup interface or `VACUUM INTO` for a safe live snapshot.
- The repository would mix the editable source, runtime indexes, schema migrations, and database housekeeping state.

For this catalog size, YAML is the simpler source of truth. If the editor later needs full-text search or much larger collections, it may build a gitignored SQLite cache from `quotes.yaml`; that cache remains disposable and never becomes the published contract.

## Styling language

Support a small, explicit inline language rather than general Markdown:

- `_italic_`
- `**bold**`
- `__underline__`
- `==red==`
- `\\_`, `\\*`, `\\=`, and `\\\\` to escape delimiter characters

Apply it to `quote` and `explanation`. Attribution and source use fixed styles so metadata remains visually consistent. Version one accepts non-overlapping spans; malformed or unclosed delimiters render literally. This avoids silently losing text and avoids claiming support for Markdown features the display cannot render.

Bundle a font family with regular, italic, bold, and bold-italic faces. Do not depend on fonts installed by a particular OS image. Underline is drawn by the renderer. Red text is placed only in the red bitplane; black and red masks must never mark the same pixel.

## Layout

Use a landscape 800×480 canvas for V2/V3, or 640×384 for V1. Rotation (`0` or `180`) is configurable for the physical frame.

The layout has three regions:

1. Quote: largest text, vertically balanced in the available space.
2. Attribution/source: smaller text immediately below the quote, separated by an em dash or line break.
3. Explanation: optional smaller text at the bottom, separated by a thin rule.

The renderer tries quote font sizes from largest to smallest while reserving measured space for all present metadata. It must never crop or silently truncate. If an entry cannot fit at the configured minimum sizes, it is invalid: log its `id`, skip it, and continue to the next valid quote. A desktop preview command writes a PNG using the same layout before content is deployed.

## Software shape

Use Python 3 for both applications. Keep them in one application repository with one shared domain package:

```text
apps/
  frame/
    app.py            # timer, GitHub polling, cache, display lifecycle
    display.py        # V1 and V2/V3 Waveshare adapters
    buttons.py        # active-low input events and debounce
    state.py          # atomic persistent device state
  studio/
    app.py            # local HTTP application
    routes.py         # list, create, edit, delete, preview, publish
    templates/        # server-rendered pages
    static/           # small local stylesheet and interactions
epigraph_core/
  catalog.py          # YAML load, schema validation, deterministic atomic save
  model.py            # Quote and Catalog domain types
  markup.py           # constrained inline syntax -> styled runs
  layout.py           # measured e-ink layout -> black/red frame
  publisher.py        # bounded Git status, commit, and push workflow
  preview.py          # hardware-free PNG renderer
config.example.toml
scripts/install.sh
scripts/update.sh
systemd/epigraph-frame.service
```

The local editor is server-rendered Python with small progressive interactions; it does not require a Node build or a separate frontend deployment. Bind to `127.0.0.1` by default. Binding to the LAN requires authentication and is outside the initial scope.

Keep the external seams small:

- `CatalogStore.load()/save(catalog)`: validates and atomically reads/writes `quotes.yaml`.
- `Publisher.publish() -> PublishedRevision`: verifies repository state, validates and previews every enabled quote, commits only `quotes.yaml`, pushes, and returns the commit SHA.
- `Renderer.render(quote, display_profile) -> Frame`: returns disjoint black/red one-bit planes or a fit error.
- `Display.show(frame)`: initializes, refreshes, waits for BUSY with a timeout, then sleeps/powers down the panel.
- `Navigator.previous()/next()/current()`: preserves ID-based position across catalog changes.

There are two real display adapters because V1 and V2/V3 use different Waveshare protocols. The shared package owns quote meaning, validation, markup, ordering, and e-ink preview; browser presentation and hardware output remain application-specific.

### Local web editor behavior

The editor provides:

- A searchable quote list with enabled/disabled and unpublished-state indicators.
- Add, edit, duplicate, enable/disable, reorder, and delete operations.
- A browser preview and an exact e-ink PNG preview using the shared renderer.
- A Publish screen showing validation results and the pending `quotes.yaml` diff.
- The last published commit SHA and any push error without exposing credentials.

Publish is deliberately bounded:

1. Acquire the editor's single-writer lock.
2. Fetch `origin/main`; refuse to publish if the remote changed since the Draft Catalog's base commit.
3. Validate the entire catalog and render every enabled quote.
4. Stage only `quotes.yaml`, create a fixed-format content commit, and push `main`.
5. Report the published commit SHA. If push fails, preserve the Draft Catalog and show the exact recoverable error.

Use the local system Git client with argument arrays and the user's existing SSH credential mechanism. The web application must not accept arbitrary Git arguments, store a GitHub token, or invoke a shell.

## Runtime behavior

1. Boot under `systemd`.
2. Load configuration, cached quote set, and last displayed quote ID.
3. Start from cache immediately if present; network availability must not block boot.
4. Fetch `quotes.yaml` through GitHub's repository contents endpoint without authentication. Send GitHub's raw media type, API version, a descriptive `User-Agent`, and the cached `ETag` through `If-None-Match`. On `304 Not Modified`, retain the current snapshot without reparsing. On `200`, safely parse YAML, validate the schema version, allowed fields, unique IDs, required text, boolean values, and markup, then write the new snapshot atomically. On fetch, rate-limit, or whole-file validation failure, keep the previous snapshot and honor GitHub's retry/reset headers.
5. Render and show the current quote. Put the panel to sleep after BUSY clears.
6. Refresh content every 15 minutes. Four unauthenticated requests per hour remain below GitHub's documented limit of 60 requests per hour for public data.
7. Rotate every 60 minutes by default. Previous/next wrap, persist the selected ID, and reset the timer.
8. If a changed feed removes the current ID, select the next valid item by the old position; if no content is usable, keep the last physical image and log the error.

Panel operations have a bounded BUSY timeout. A timeout causes one driver reset and retry; a second failure leaves the last physical image intact and reports a service error. Do not loop refreshes indefinitely.

## Configuration

Keep operator configuration outside the application checkout at `/etc/epigraph/frame.toml`:

```toml
github_owner = "jonasbaldwin"
github_repository = "epigraph-catalog"
github_ref = "main"
github_content_path = "quotes.yaml"
panel = "7in5b_v2"       # use 7in5b_v1 only after checking the label
rotation_minutes = 60
content_refresh_minutes = 15
rotation_degrees = 0
previous_gpio = 5
next_gpio = 6
busy_timeout_seconds = 45
```

The install script copies `config.example.toml` only when no configuration exists, so updates cannot overwrite local settings. The public content repository requires no access token, credentials file, or Git checkout on the display. Do not add a GitHub token unless the repository is intentionally made private in a future design.

Configure Wi-Fi, hostname, SSH key, locale, and user through Raspberry Pi Imager before first boot. Disable password SSH after key access works.

## Installation and updates

Initial installation:

1. Flash Raspberry Pi OS Lite. Use 32-bit for an original Zero W; either supported architecture is suitable for Zero 2 W.
2. Boot, enable SPI, and run the exact official Waveshare sample for the identified panel revision. Do not proceed until a black/red/white test image completes and the driver sleeps cleanly.
3. Install the application under `/opt/epigraph`, create a dedicated unprivileged `epigraph` user with only the required GPIO/SPI group access, create a virtual environment, and enable `epigraph-frame.service`.
4. Fetch the current `quotes.yaml` file and render every enabled quote with the preview command before enabling rotation.

Application updates are manual and explicit over SSH:

```text
ssh epigraph-frame
sudo /opt/epigraph/current/scripts/update.sh <release-tag>
```

The update script downloads a tagged release, installs dependencies into a new virtual environment, runs the hardware-free configuration/feed/preview checks, atomically switches the `current` symlink, restarts the service, and rolls back the symlink if the service health check fails. Do not auto-update application code unattended. Apply OS security updates using the OS's normal supported mechanism.

Routine quote edits happen in Epigraph Studio and reach GitHub only through Publish. Routine timing/orientation changes happen in `/etc/epigraph/frame.toml`, followed by `sudo systemctl restart epigraph-frame`. Device logs are available through `journalctl -u epigraph-frame`.

## Delivery sequence and acceptance checks

### 1. Hardware proof

- Confirm panel and driver board revision labels.
- Official Waveshare sample renders black, red, and white correctly.
- Panel enters sleep after refresh and retains the image.
- Previous and next electrical inputs read reliably without false presses.

### 2. Shared quote package

- Safely parse the versioned YAML schema and reject unknown or mistyped fields.
- Parse all four markup forms and escapes.
- Deterministically save YAML through an atomic file replacement.
- Generate preview PNGs with black/red masks matching the panel profile.
- Long content either fits by deterministic font reduction or fails explicitly; no pixels leave the canvas.

### 3. Local web editor

- Add, edit, duplicate, enable/disable, reorder, and delete quotes without committing them.
- Clearly distinguish Draft and Published Catalogs.
- Survive process restart with uncommitted Draft Catalog changes intact.
- Preview the browser and exact e-ink render before publishing.
- Refuse Publish on schema/render errors, unexpected changed files, or remote divergence.
- Commit only `quotes.yaml`, push successfully, and report the resulting commit SHA.
- Preserve the Draft Catalog and report a recoverable error when push fails.

### 4. Device application

- Device boots directly to the last valid cached quote without a network connection.
- A valid `quotes.yaml` publish is picked up without redeploying code.
- A malformed or unavailable content file does not erase the cache or the display.
- Timer cycles and wraps in YAML list order.
- Previous/next wrap, reset the timer, survive process restart, and do not create an unbounded refresh queue.
- Every completed update sleeps/powers down the panel.

### 5. Operational handoff

- Install and update scripts work from a fresh OS image.
- Configuration survives an application update.
- Failed application update rolls back to the prior release.
- Local Git authentication works without storing credentials in the web application.
- The frame protects the glass and flex cable, leaves button access, and permits SD/power maintenance.

## Risks and decisions still to confirm during the hardware proof

| Item | Status | Risk / verification owner |
|---|---|---|
| Raw panel needs a driver HAT | Confirmed | Waveshare product documentation. |
| Exact panel revision | Blocked on physical label | Owner photographs label/flex code; implementer selects V1 or V2/V3 driver. |
| 800×480 resolution | Risky until label check | Indexed listing says 800×480, but Waveshare documents an older 640×384 V1. |
| Partial refresh | Intentionally not used | Source material differs and red updates require full refresh anyway. |
| Public quote repository | Confirmed | Quotes and commit history are public; never commit private material or credentials. |
| Local publisher | Single writer | Publishing refuses remote divergence instead of attempting an automatic merge. |
| Controller | Recommended: Zero 2 W | Original Zero W acceptable; Pico W only with remote pre-rendering. |
| Rotation interval | Assumed 60 minutes | Configurable; must remain at least 3 minutes per manufacturer guidance. |

## Sources

Primary sources:

- [Waveshare 7.5-inch e-Paper (B) raw panel](https://www.waveshare.com/7.5inch-e-paper-b.htm): raw-panel status, color, resolution, SPI, dimensions, refresh behavior, and recommended driver boards.
- [Waveshare Universal e-Paper Driver HAT](https://www.waveshare.com/e-paper-driver-hat.htm): compatibility, interface signals, voltage translation, and driver-HAT requirement.
- [Waveshare 7.5-inch e-Paper HAT (B) manual](https://www.waveshare.com/wiki/7.5inch_e-Paper_HAT_(B)_Manual): V1/V2/V3 distinction, Pi GPIO mapping, display precautions, minimum refresh interval, and sleep requirement.
- [Waveshare official e-Paper driver repository](https://github.com/waveshareteam/e-Paper/tree/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd): V1 and V2 Python driver modules.
- [Raspberry Pi Zero 2 W specification](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/): CPU, 512 MB RAM, Wi-Fi, and 40-pin footprint.
- [Raspberry Pi Pico W specification](https://www.raspberrypi.com/products/raspberry-pi-pico/): RP2040, 264 kB SRAM, 2 MB flash, Wi-Fi, and SPI capabilities.
- [GitHub repository contents endpoint](https://docs.github.com/en/rest/repos/contents#get-repository-content): public content retrieval, raw media response, conditional `304` response, and endpoint shape.
- [GitHub REST rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api): unauthenticated public-data limit of 60 requests per hour and rate-limit response headers.
- [GitHub large-file guidance](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github): file limits, repository-size guidance, and Git's unsuitability as a database backup mechanism.
- [SQLite corruption guidance](https://www.sqlite.org/howtocorrupt.html#_backup_or_restore_while_a_transaction_is_active): risks of copying a live database and safe snapshot mechanisms.

Secondary identification source:

- [CamelCamelCamel record for ASIN B076BVFHDV](https://camelcamelcamel.com/product/B076BVFHDV): identifies the unavailable Amazon listing as a Waveshare 7.5-inch red/black/white raw panel. This was used only to resolve the ASIN because Amazon's page was not retrievable; the hardware claims above are verified against Waveshare.

## Research limitations

The physical panel and driver board were not available for inspection. The exact revision, flex-cable identifier, connector orientation, and whether a driver HAT was included must be verified on the hardware before power is applied. Product specifications and OS installation details are date-sensitive and should be checked again when implementation begins.
