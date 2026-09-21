# Epigraph

Epigraph manages a personal catalog of styled quotations, previews unpublished changes in Epigraph Studio, publishes reviewed content to GitHub, and presents the Published Catalog through a browser and an Epigraph Frame e-ink display.

## Connect the e-ink display

The Waveshare 7.5-inch (B) raw e-paper panel must not connect directly to the Raspberry Pi GPIO header. Insert the panel's flex cable into a compatible Waveshare Universal e-Paper Driver HAT, then either stack the HAT on the Pi's 40-pin header or use the following connections. Power off the Pi before inserting or removing the flex cable.

`BCM` numbers are the GPIO identifiers used in software; physical pin numbers identify positions on the Pi's 40-pin header.

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

If an older driver board has only eight connections and no `PWR`, omit `PWR` and use the matching older Waveshare configuration. Do not power the HAT separately while it is also powered through the Pi header. See [`docs/epigraph-plan.md`](docs/epigraph-plan.md#recommended-wiring-pi-zero) for the complete hardware plan, including rotary-encoder and PIR wiring.

### Test the display with Waveshare's sample

First check the label on the back of the panel. Use the V2 sample for an 800×480 **V2 or V3** panel; V3 is compatible with the V2 driver. Use the legacy B/C sample for a 640×384 **V1** panel. Do not guess the revision or run the wrong sample.

Enable SPI once:

```sh
sudo raspi-config
```

Choose **Interface Options → SPI → Yes**, finish, and reboot:

```sh
sudo reboot
```

Reconnect over SSH and confirm that SPI is available:

```sh
ls /dev/spidev0.0 /dev/spidev0.1
```

Install the sample's dependencies and download Waveshare's official repository. Skip the `git clone` command if `~/e-Paper` already exists.

```sh
sudo apt update
sudo apt install -y git fonts-dejavu-core fonts-dejavu-extra python3-pil python3-numpy python3-spidev python3-gpiozero python3-yaml
cd ~
git clone https://github.com/waveshareteam/e-Paper.git
cd ~/e-Paper/RaspberryPi_JetsonNano/python/examples
```

Run the sample matching the panel label:

```sh
# V2 or V3: 800×480
python3 epd_7in5b_V2_test.py

# V1 only: 640×384
python3 epd_7in5bc_test.py
```

Run only one of those commands. A successful test cycles through black, red, and white images, clears the panel, and puts the driver to sleep. The final blank screen is therefore expected. Flickering during a full refresh is normal, and each refresh can take about 26 seconds. After the test finishes, wait at least 180 seconds before running it again.

If the program hangs while reporting that the display is busy, power off the Pi and recheck the flex-cable orientation, panel revision, and especially the `BUSY`, `RST`, and `PWR` connections. Never move wiring or the flex cable while powered.

These steps follow Waveshare's [7.5-inch e-Paper HAT (B) manual](https://www.waveshare.com/wiki/7.5inch_e-Paper_HAT_(B)_Manual) and use its [official Python samples](https://github.com/waveshareteam/e-Paper/tree/master/RaspberryPi_JetsonNano/python/examples).

### Check the PIR and rotary encoder wiring

After the revision-matched Waveshare sample succeeds, run the standalone wiring check from this repository:

```sh
python3 scripts/check_hardware.py
```

The 800×480 display is split into a PIR region and an encoder region. Motion changes the PIR state to `ON`; it returns to `OFF` after five minutes without an active motion signal. Complete encoder detents report `CLOCKWISE` or `COUNTER-CLOCKWISE`, and the integrated switch reports `BUTTON PRESSED`. Counts help reveal duplicate or missing events. The terminal mirrors each event immediately.

Every wiring-check screen uses a standard full refresh, and the program returns the panel to sleep after each update. Press `Ctrl+C` to stop. If the reported directions are reversed, power off the Pi before swapping the encoder's A and B wires.

For a quick PIR timeout check, override five minutes temporarily:

```sh
python3 scripts/check_hardware.py --pir-timeout-seconds 10
```

To inspect the layout without GPIO or a display:

```sh
python3 scripts/check_hardware.py --preview hardware-check.png
```

### Run the initial quote display

After the wiring check succeeds, start the hardware-first quote display:

```sh
python3 scripts/display_quotes.py
```

The complete local catalog is defined in [`quotes.yaml`](quotes.yaml), including the previously added Kathryn Schulz and Charles Renouvier quotations. The display validates and loads this canonical YAML file at startup. Quote type scales down from the largest measured size that fits, so longer entries wrap naturally without cropping. Quotes change every two minutes while motion has occurred within the last 15 minutes. Clockwise selects the next quote and counter-clockwise selects the previous quote even while automatic rotation is paused.

Quote text supports `**bold**`, `_italic_`, and `==red highlight==` markup. Styles may be nested or combined; for example, `**_wrong_**` renders as bold italic text and `==**right**==` renders as bold red text. Unmatched markers are displayed literally.

The first update and every quote change use a standard full refresh. Hardware UAT confirmed that this panel's partial path retains overlapping quotes and that its fast mode still presents a full-screen flash. The panel sleeps after every update.

Encoder controls:

- Short press: fully refresh into settings, or fully refresh back to the quote.
- Turn in settings: adjust minutes per quote (`n`).

The selected quote and rotation setting persist in `~/.local/state/epigraph/frame.json`. Press `Ctrl+C` to stop and cleanly close the display.
Last motion and last refresh appear together on one footer line.

Render every quote and the settings screen without GPIO:

```sh
python3 scripts/display_quotes.py --preview-dir previews
```

### Add a quote and update the Pi

Run the interactive add command from the repository on the development computer:

```sh
python3 scripts/add_quote.py --user <username> --host <hostname-or-ip>
```

It prompts for the quote, author, source, and notes. Source and notes may be left blank. Omit `--user` or `--host` to be prompted for the missing SSH detail, and use `--port <number>` when SSH is not listening on port 22.

The command validates and atomically appends the quote to `quotes.yaml`, then uses the secure SSH updater below. A successful update restarts Epigraph Frame and verifies that it remains active, so the new quote is available immediately. If the Pi update fails, the quote remains in the local catalog; follow the reported retry command instead of adding it again.

### Update the Pi over SSH

From the repository on the development computer, provide the SSH username and Pi hostname or address:

```sh
python3 scripts/update_pi.py --user <username> --host <hostname-or-ip>
```

Omit either value to be prompted:

```sh
python3 scripts/update_pi.py
```

Use `--port <number>` only when SSH is not listening on port 22. There is deliberately no password argument. If authentication needs a password, the system OpenSSH client asks for it directly; enter a password only in that prompt.

The updater preserves normal SSH host-key verification, reuses one temporary SSH connection, syntax-checks the runtime Python files, and atomically installs them under `~/epigraph/scripts` on the Pi. It also installs and enables the user-level `epigraph-frame.service`. If user lingering is not already active, remote `sudo` asks for the Pi password on its own terminal prompt and enables startup without an SSH login.

After installation, the updater restarts `epigraph-frame.service`, waits briefly, and verifies that it remains active. If activation fails, it restores the previous scripts, catalog, and unit, then restores the previous service state. When the updater returns successfully, the new script is already running; no separate SSH command or Pi reboot is required.

### Verify automatic startup

Running the updater performs the automatic-startup setup and starts the frame. No additional file copying, service command, or reboot is required for a normal script update.

To inspect the resulting state:

```sh
ssh <username>@<hostname-or-ip> 'systemctl --user is-enabled epigraph-frame.service'
ssh <username>@<hostname-or-ip> 'systemctl --user is-active epigraph-frame.service'
ssh <username>@<hostname-or-ip> 'loginctl show-user "$(id -un)" -p Linger'
```

The expected results are `enabled`, `active`, and `Linger=yes`. A reboot is only useful as a one-time test of the power-on path:

```sh
ssh -t <username>@<hostname-or-ip> 'sudo reboot'
```

After the Pi returns to the network, `systemctl --user is-active epigraph-frame.service` should still report `active`. Inspect status and logs if it does not:

```sh
ssh <username>@<hostname-or-ip> 'systemctl --user status epigraph-frame.service'
ssh <username>@<hostname-or-ip> 'journalctl --user -u epigraph-frame.service'
```

## Connect to Epigraph Frame over SSH

The computer and Raspberry Pi must be on the same local network. The Pi Zero W supports 2.4 GHz Wi-Fi; the computer may use another band if both bands share the same non-isolated LAN.

When preparing the SD card in Raspberry Pi Imager, configure:

- a hostname;
- the Wi-Fi SSID, password, and country;
- a username and password; and
- **Remote Access → Enable SSH → Use password authentication**.

After powering the Pi, allow about two minutes for its first boot.

### Connect by hostname

Use the username and hostname configured in Raspberry Pi Imager:

```sh
ssh <username>@<hostname>.local
```

For example, with hostname `epigraph`:

```sh
ssh <username>@epigraph.local
```

On the first connection, verify the presented host and type `yes` to accept its host key. Enter the configured password when prompted. The terminal does not display characters while a password is entered.

### Connect by IP address

If the `.local` hostname does not resolve, find the Pi in the router's connected-device or DHCP-leases page. Check that it is reachable and that SSH is listening:

```sh
ping -c 3 <pi-ip-address>
nc -vz <pi-ip-address> 22
```

Then connect:

```sh
ssh <username>@<pi-ip-address>
```

The address is assigned by DHCP and may change. Prefer the `.local` hostname or configure a DHCP reservation in the router.

### Troubleshooting

- **`Connection refused`:** the Pi is reachable, but SSH is not listening. Power down the Pi, mount the SD card's `bootfs` partition on a Mac, create the SSH enablement marker, and eject the card cleanly:

  ```sh
  touch /Volumes/bootfs/ssh
  diskutil eject /Volumes/bootfs
  ```

  Reinsert the card, boot the Pi, wait about two minutes, and test port 22 again.

- **Connection timeout:** verify the IP address, Wi-Fi credentials, and country setting. Ensure neither device is on a guest network and disable AP/client/wireless isolation. The Pi Zero W requires 2.4 GHz Wi-Fi.
- **`Permission denied`:** verify the exact username and password configured in Raspberry Pi Imager.
- **Host-key warning after intentionally reimaging the Pi:** remove the old key only after confirming that the address still belongs to this Pi:

  ```sh
  ssh-keygen -R <pi-ip-address>
  ```

Port forwarding is not required for local access and must not be used to expose port 22 directly to the public internet.
