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

Use a standard full refresh for every quote change. Hardware UAT exhausted the official partial helper, corrected buffer polarity, inverse scrubbing, and explicit old/new controller-plane writes; the panel still transitioned `A → inverse(A) → A+B`. The driver's fast mode was also rejected because it still presents a full-screen flash. This installed V3 panel therefore has no acceptable intermediate refresh mode.

Waveshare recommends at least 180 seconds between refreshes and requires the panel to be put to sleep or powered off when it is not refreshing. The operator-approved two-minute active rotation is a hardware-trial setting. Rotary-encoder turns request an immediate standard full refresh.

## Parts

Required:

- Existing Waveshare 7.5-inch e-Paper (B) raw panel.
- Waveshare Universal e-Paper Driver HAT.
- Raspberry Pi Zero 2 W with a soldered 40-pin male header; an original Zero W is acceptable.
- Reliable 5 V micro-USB supply. The official 12.5 W Zero supply is ample.
- microSD card.
- One mechanical incremental rotary encoder with quadrature A/B outputs and an integrated normally-open push switch.
- One three-wire PIR motion sensor whose digital output is guaranteed to be 3.3 V safe.
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

### Rotary encoder and PIR motion sensor

Use a mechanical incremental rotary encoder with A/B quadrature outputs and a push switch. Connect the encoder common and one side of its push switch to ground and use the Pi's internal pull-ups. The encoder replaces the separate Previous and Next buttons.

| Signal | BCM | Physical pin | Other connection |
|---|---:|---:|---|
| Encoder A | 5 | 29 | Encoder common to GND, physical pin 30 |
| Encoder B | 6 | 31 | Encoder common to GND, physical pin 30 |
| Encoder push switch | 13 | 33 | GND, physical pin 34 |
| PIR OUT | 16 | 36 | — |
| PIR VCC | — | 2 (5 V) | — |
| PIR GND | — | 39 | — |

Only connect PIR OUT directly after verifying from the selected sensor's datasheet or a meter that its high level does not exceed the Pi's 3.3 V GPIO limit. Use a level shifter if it can output 5 V.

One complete clockwise detent requests Next and one complete counter-clockwise detent requests Previous. Swap the A/B assignments if the installed encoder runs backward. Decode the quadrature sequence and emit only complete detents so contact bounce or invalid transitions do not produce extra navigation. A turn wraps through catalog order, persists the selected ID, resets the automatic rotation timer, and performs a standard full refresh. Turns received while the panel is busy are coalesced to the final requested destination rather than starting unbounded refreshes.

Initialize and debounce the encoder push switch. A press performs a standard full refresh and toggles between the quote and settings screens. In settings, rotation adjusts minutes per quote (`n`), and another press fully refreshes back to the quote.

A PIR reports recent motion, not reliable room occupancy. Treat the room as active from the latest motion event through a configurable inactivity grace period, defaulting to 15 minutes. Automatic quote rotation is paused at startup and whenever that grace period expires. Motion restarts a full rotation interval; it does not immediately change the quote. Content polling and manual encoder navigation continue while automatic rotation is paused, and the e-ink panel retains its current image.

## Pico W alternative

A Pico W is electrically capable. One valid SPI0 mapping to the Universal Driver HAT is:

| Signal | Pico W GPIO | Pico physical pin |
|---|---:|---:|
| Driver HAT VCC | 3V3 OUT | 36 |
| Driver HAT GND | GND | 38 |
| Driver HAT DIN | GP19 / SPI0 TX | 25 |
| Driver HAT CLK | GP18 / SPI0 SCK | 24 |
| Driver HAT CS | GP17 | 22 |
| Driver HAT DC | GP20 | 26 |
| Driver HAT RST | GP21 | 27 |
| Driver HAT BUSY | GP22 | 29 |
| Driver HAT PWR | GP16 | 21 |
| Encoder A | GP14 | 19 |
| Encoder B | GP15 | 20 |
| Encoder common | GND | 18 |
| Encoder push switch | GP13 and GND | 17 and 18 |
| PIR OUT | GP12 | 16 |
| PIR VCC | VBUS (5 V) | 40 |
| PIR GND | GND | 38 |

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

The layout has four regions:

1. Quote: largest text, horizontally centered without decorative quotation marks, normally wrapped, and vertically balanced in the available space.
2. Attribution/source: smaller, right-aligned text immediately below the quote, separated by an em dash or line break.
3. Explanation: optional smaller, right-aligned text near the bottom, separated by a thin rule.
4. Status: last motion and last refresh in small text on one footer line.

The renderer measures content and tries quote font sizes from largest to smallest while reserving space for all present metadata. Short quotes therefore use larger type and longer quotes scale down deterministically. It must never crop or silently truncate. If an entry cannot fit at the configured minimum size, it is invalid: log its `id`, skip it, and continue to the next valid quote. A desktop preview command writes a PNG using the same layout before content is deployed.

## Software shape

Use Python 3 for both applications. Keep them in one application repository with one shared domain package:

```text
apps/
  frame/
    app.py            # timer, GitHub polling, cache, display lifecycle
    display.py        # V1 and V2/V3 Waveshare adapters
    inputs.py         # rotary encoder, reserved push switch, and PIR events
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

1. Boot under the enabled `epigraph-frame.service`; the initial hardware-first deployment uses a lingering user systemd manager so no interactive login is required.
2. Load configuration, cached quote set, and last displayed quote ID.
3. Start from cache immediately if present; network availability must not block boot.
4. Fetch `quotes.yaml` through GitHub's repository contents endpoint without authentication. Send GitHub's raw media type, API version, a descriptive `User-Agent`, and the cached `ETag` through `If-None-Match`. On `304 Not Modified`, retain the current snapshot without reparsing. On `200`, safely parse YAML, validate the schema version, allowed fields, unique IDs, required text, boolean values, and markup, then write the new snapshot atomically. On fetch, rate-limit, or whole-file validation failure, keep the previous snapshot and honor GitHub's retry/reset headers.
5. Render and show the current quote. Put the panel to sleep after BUSY clears.
6. Refresh content every 15 minutes. Four unauthenticated requests per hour remain below GitHub's documented limit of 60 requests per hour for public data.
7. Rotate every two minutes by default only while the PIR presence gate is active. At startup or after 15 minutes without motion by default, pause the automatic timer without changing the retained image. On motion, start a new full rotation interval rather than immediately changing the quote.
8. Clockwise/counter-clockwise encoder detents select next/previous and wrap in catalog order. Manual navigation remains available while automatic rotation is paused, persists the selected ID, resets the timer baseline, and performs a standard full refresh.
9. Perform a standard full refresh for every quote change and every transition into or out of settings.
10. If a changed feed removes the current ID, select the next valid item by the old position; if no content is usable, keep the last physical image and log the error.

Panel operations have a bounded BUSY timeout. A timeout causes one driver reset and retry; a second failure leaves the last physical image intact and reports a service error. Do not loop refreshes indefinitely.

## Configuration

Keep operator configuration outside the application checkout at `/etc/epigraph/frame.toml`:

```toml
github_owner = "jonasbaldwin"
github_repository = "epigraph-catalog"
github_ref = "main"
github_content_path = "quotes.yaml"
panel = "7in5b_v2"       # use 7in5b_v1 only after checking the label
rotation_minutes = 2
content_refresh_minutes = 15
rotation_degrees = 0
encoder_a_gpio = 5
encoder_b_gpio = 6
encoder_switch_gpio = 13 # press: settings/quote
pir_gpio = 16
pir_inactivity_minutes = 15
busy_timeout_seconds = 45
```

The initial hardware-first script persists on-device changes to `rotation_minutes` and the selected quote under the invoking user's local state directory. Legacy `full_refresh_every` state is ignored. The later managed service must give its runtime user a stable writable state directory.

The install script copies `config.example.toml` only when no configuration exists, so updates cannot overwrite local settings. The public content repository requires no access token, credentials file, or Git checkout on the display. Do not add a GitHub token unless the repository is intentionally made private in a future design.

Configure Wi-Fi, hostname, SSH key, locale, and user through Raspberry Pi Imager before first boot. Disable password SSH after key access works.

## Installation and updates

Initial installation:

1. Flash Raspberry Pi OS Lite. Use 32-bit for an original Zero W; either supported architecture is suitable for Zero 2 W.
2. Boot, enable SPI, and run the exact official Waveshare sample for the identified panel revision. Do not proceed until a black/red/white test image completes and the driver sleeps cleanly.
3. Install the application under `/opt/epigraph`, create a dedicated unprivileged `epigraph` user with only the required GPIO/SPI group access, create a virtual environment, and enable `epigraph-frame.service`.
4. Fetch the current `quotes.yaml` file and render every enabled quote with the preview command before enabling rotation.

The initial hardware-first SSH updater installs the runtime scripts under `~/epigraph/scripts`, installs and enables a user unit under `~/.config/systemd/user`, and enables logind lingering through remote `sudo`. Every successful deployment restarts the managed frame and verifies that it remains active; activation failure restores the previous deployment and service state. A full Pi reboot is unnecessary for a normal script update. The `/opt/epigraph` release layout above remains the later production cutover.

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
- Clockwise and counter-clockwise encoder detents read reliably as exactly one event, the push switch produces no action, and PIR motion/inactivity transitions are stable.

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

- Device boots directly through the enabled systemd unit to the last valid cached quote without a network connection.
- A valid `quotes.yaml` publish is picked up without redeploying code.
- A malformed or unavailable content file does not erase the cache or the display.
- The timer cycles and wraps in YAML list order only while the PIR presence gate is active.
- Clockwise/counter-clockwise detents wrap, reset the timer baseline, survive process restart, and do not create an unbounded refresh queue.
- PIR inactivity pauses automatic rotation without blanking the retained image; motion starts a full interval, and manual navigation still works while paused.
- Every completed update sleeps/powers down the panel.

### 5. Operational handoff

- Install and update scripts work from a fresh OS image.
- Configuration survives an application update.
- Failed application update rolls back to the prior release.
- Local Git authentication works without storing credentials in the web application.
- The frame protects the glass and flex cable, exposes the encoder, gives the PIR a clear view of the room, and permits SD/power maintenance.

## Risks and decisions still to confirm during the hardware proof

| Item | Status | Risk / verification owner |
|---|---|---|
| Raw panel needs a driver HAT | Confirmed | Waveshare product documentation. |
| Exact panel revision | Confirmed: V3 | Rear label and successful V2-compatible Waveshare sample. |
| 800×480 resolution | Confirmed | Installed V3 panel and rendered 800×480 hardware diagnostic. |
| Full-screen partial refresh | Unsupported on installed panel | Official and explicit-plane partial paths both ended at `A+B`; standard full refresh is required for every quote change. |
| Public quote repository | Confirmed | Quotes and commit history are public; never commit private material or credentials. |
| Local publisher | Single writer | Publishing refuses remote divergence instead of attempting an automatic merge. |
| Controller | Recommended: Zero 2 W | Original Zero W acceptable; Pico W only with remote pre-rendering. |
| Rotation interval | Confirmed: 2 minutes | Each active rotation performs a standard full refresh; treat the two-minute interval as a hardware trial against Waveshare's longer refresh guidance. |
| Encoder direction and detents | Confirmed by operator | Hardware diagnostic reports clockwise/counter-clockwise and complete detents correctly. |
| Encoder push action | Confirmed | Press toggles settings/quote with a standard full refresh; rotation in settings adjusts `n`. |
| PIR inactivity grace | Assumed 15 minutes | Configurable; tune in the installed room because PIR detects motion, not continued presence. |
| PIR electrical output | Blocked on selected sensor | Verify OUT is 3.3 V safe before connecting it to the Pi. |

## Sources

Primary sources:

- [Waveshare 7.5-inch e-Paper (B) raw panel](https://www.waveshare.com/7.5inch-e-paper-b.htm): raw-panel status, color, resolution, SPI, dimensions, refresh behavior, and recommended driver boards.
- [Waveshare Universal e-Paper Driver HAT](https://www.waveshare.com/e-paper-driver-hat.htm): compatibility, interface signals, voltage translation, and driver-HAT requirement.
- [Waveshare 7.5-inch e-Paper HAT (B) manual](https://www.waveshare.com/wiki/7.5inch_e-Paper_HAT_(B)_Manual): V1/V2/V3 distinction, Pi GPIO mapping, display precautions, minimum refresh interval, and sleep requirement.
- [Waveshare official e-Paper driver repository](https://github.com/waveshareteam/e-Paper/tree/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd): V1 and V2 Python driver modules.
- [Raspberry Pi Zero 2 W specification](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/): CPU, 512 MB RAM, Wi-Fi, and 40-pin footprint.
- [Raspberry Pi Pico W specification](https://www.raspberrypi.com/products/raspberry-pi-pico/): RP2040, 264 kB SRAM, 2 MB flash, Wi-Fi, and SPI capabilities.
- [GPIO Zero input-device API](https://gpiozero.readthedocs.io/en/stable/api_input.html): BCM numbering, rotary-encoder A/B/common wiring and directional events, button pull-up/debounce behavior, and typical PIR VCC/OUT/GND wiring and motion events.
- [GitHub repository contents endpoint](https://docs.github.com/en/rest/repos/contents#get-repository-content): public content retrieval, raw media response, conditional `304` response, and endpoint shape.
- [GitHub REST rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api): unauthenticated public-data limit of 60 requests per hour and rate-limit response headers.
- [GitHub large-file guidance](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github): file limits, repository-size guidance, and Git's unsuitability as a database backup mechanism.
- [SQLite corruption guidance](https://www.sqlite.org/howtocorrupt.html#_backup_or_restore_while_a_transaction_is_active): risks of copying a live database and safe snapshot mechanisms.

Secondary identification source:

- [CamelCamelCamel record for ASIN B076BVFHDV](https://camelcamelcamel.com/product/B076BVFHDV): identifies the unavailable Amazon listing as a Waveshare 7.5-inch red/black/white raw panel. This was used only to resolve the ASIN because Amazon's page was not retrievable; the hardware claims above are verified against Waveshare.

## Research limitations

The physical panel and driver board were not available for inspection. The exact revision, flex-cable identifier, connector orientation, and whether a driver HAT was included must be verified on the hardware before power is applied. Product specifications and OS installation details are date-sensitive and should be checked again when implementation begins.
