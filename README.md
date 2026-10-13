# Toronto Postcard

A Mac publisher and lightweight Raspberry Pi receiver for the **Pimoroni Inky Impression 5.7-inch, seven-color, 600 × 448** display. The publisher renders the selected Toronto postcard design in landscape, with Toronto's skyline, current weather, today's calendar, and a subtle red marker for the next event. An ongoing event is labeled NOW.

The Mac keeps calendar credentials and fetches data; each Pi receives only an indexed PNG over SSH. The receiver uses the Pi's existing SSH service; it adds no HTTP server or additional listening port. No calendar account is needed on the Pi. Additional Pis are entries in `config.json`.

## Runtime behavior

- A launchd agent runs the publisher every **30 minutes** while its Mac user is logged in and the Mac is awake. It also publishes immediately when installed or loaded at login. If the Mac is asleep or logged out, the existing image stays on the Pi until publishing resumes. There is no dependency on Codex after setup.
- Calendar feeds and current conditions/hourly forecasts from [Open-Meteo](https://open-meteo.com/en/docs) are fetched each scheduled run. The default weather cache interval is also 30 minutes. Current weather is weather-model data, not a local thermometer reading. The display credits Open-Meteo.
- Calendar recurring events, recurrence exceptions, all-day dates, overnight events, and Toronto daylight-saving changes are handled. Declined invitations are excluded when the feed owner's email is configured.
- Up to two all-day entries and four ongoing/upcoming timed events fit. Ended timed events disappear on the next refresh; the omitted-entry count includes only remaining events. Once all timed events have ended, the calendar says “No more events today.” Each refresh fetches the feeds and recalculates the list, so later additions can appear even after that message was shown. Long titles wrap to two lines, then truncate.
- Identical images are skipped. The receiver also checks for identical images and shares the existing artwork helper's lock and 180-second cooldown. SSH allows up to 30 seconds per connection attempt and retries a failed connection once, accommodating the Pi Zero's intermittent Wi-Fi. A failed transfer or refresh is retried on the next publisher run.
- Network failures use today's cached calendar for at most 24 hours and cached weather for at most six hours. Weather cache use and unavailable sources are labeled. A small bottom-right “Last refresh” note shows the image's refresh date and time in the configured timezone, including when calendar events come from cache. A bootstrap calendar snapshot is explicitly labeled and expires at midnight; it is not treated as live calendar access.
- The receiver checks the display's driver and resolution before using its direct seven-color indices, preserving crisp text without color dithering.

## Project layout

`toronto_postcard/` contains sources, rendering, scheduling entry point, and multi-Pi publishing. `receiver/receive.py` is the Pi's forced SSH command and works with its installed Python 3.9 and Inky 1.3.2. `scripts/install_mac.py` installs the launchd agent. `tests/` checks event expansion, selection, daylight saving, native rendering, and image validation. `requirements.lock.txt` records the tested publisher dependencies.

Private deployment files are **excluded from Git**: `config.json`, `.secrets/`, `.state/`, and `.venv/`. The configuration example contains no working credentials. Live event data, rendered images, host-specific settings, and private keys must stay out of commits and bundles.

## Set up a publisher on another Mac

Requires Python 3.11 or newer, Git, and a logged-in macOS account. Python can be installed through Homebrew. All fonts are obtained from macOS; no proprietary font files are redistributed. An optional Linux publisher can use `fonts-dejavu-core`, with slightly different typography.

```sh
git clone https://github.com/palpen/toronto-postcard.git
cd toronto-postcard
python3 -m venv .venv
.venv/bin/python -m pip --isolated install --upgrade "pip>=26.2.1"
.venv/bin/python -m pip --isolated install --require-hashes -r requirements.lock.txt
cp config.example.json config.json
mkdir -m 700 .secrets
chmod 600 config.json
```

Edit `config.json` for this Mac and its Pi targets. Set a feed's `url_file` to a private file containing only the calendar's existing HTTPS iCal subscription URL. In Google Calendar, find it under Settings → the calendar → Integrate calendar → Secret address in iCal format ([Google's instructions](https://support.google.com/calendar/answer/37648)). This is a read-only credential for the calendar and belongs only in `.secrets/`, never in Git, logs, or a public calendar URL. Keep the calendar private. Add separate feeds to combine calendars.

To transfer the credential without placing it in shell history, run the one-time setup form from a computer that already has trusted SSH access to the publisher Mac:

```sh
python3 scripts/setup_feed.py --host PUBLISHER_SSH_ALIAS --destination /absolute/path/to/toronto-postcard/.secrets/personal-calendar-url
```

Open the printed localhost address and paste the private calendar link into the password field. The form sends it over SSH directly to the Mac's private file, logs no credential values, and shuts down after saving. Adapt the SSH alias and destination for your own publisher.

```sh
chmod 600 .secrets/personal-calendar-url
.venv/bin/python -m toronto_postcard.publisher --config config.json --preview-only
.venv/bin/python scripts/install_mac.py
```

On recent macOS versions, allow **Python** under **System Settings → Privacy & Security → Local Network** on the publisher Mac. A launchd run can fetch internet data but fail to reach a Pi with `No route to host` when this permission is blocked, even when a manual SSH session works. See [Apple's local-network privacy documentation](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy). After granting access, verify the scheduled job itself:

```sh
launchctl kickstart gui/$(id -u)/local.toronto-postcard.publisher
# After the refresh completes:
cat .state/status.json
launchctl print gui/$(id -u)/local.toronto-postcard.publisher
```

Expect a target status of `displayed` or `unchanged` and a last exit code of `0`. A configured schedule alone does not confirm successful delivery.

Before a feed is connected, an optional calendar bootstrap snapshot is explicitly labeled and expires at midnight. Once a feed is configured, live/cached feed data takes precedence.

## Prepare a new Pi

Inventory each Pi first: this receiver supports only the matching Inky Impression 5.7-inch panel. Set up Raspberry Pi OS, SPI/I2C and Pimoroni's Inky driver according to [Pimoroni's instructions](https://github.com/pimoroni/inky). The receiver uses the Pi's installed display driver. Keep the Pi's OS, SSH server, Pillow, and Inky driver supported and patched; the publisher lockfile does not cover them.

Using a trusted administrator connection, clone the source into `/home/pi/toronto-postcard` (or adapt the paths for a dedicated display user). The Pi does not install the Mac publisher's dependencies.

```sh
git clone https://github.com/palpen/toronto-postcard.git /home/pi/toronto-postcard
python3 -c 'from inky.auto import auto; print(auto().resolution)'
```

Create a dedicated, non-interactive key on the Mac. It will have access only to this receiver, not to the Pi's shell:

```sh
ssh-keygen -t ed25519 -N '' -C toronto-postcard-display-only -f .secrets/pi-display
```

Append the public key to the Pi user's `~/.ssh/authorized_keys`, prefixed with:

```text
restrict,command="/usr/bin/python3 /home/pi/toronto-postcard/receiver/receive.py" ssh-ed25519 PUBLIC_KEY toronto-postcard-display-only
```

Keep SSH key-only authentication and existing firewall protections. Ensure that the Mac's LAN address is permitted by the Pi's firewall. Obtain the Pi's Ed25519 host public key **through an already trusted connection or local console**. Write a known-hosts line for its configured hostname/IP to the Mac's `.secrets/pi-known-hosts`. The publisher requires strict host-key verification; it does not blindly accept network-scanned keys.

Register the prepared Pi on the publisher Mac. Give each Pi its own receiver key and pinned known-hosts file. For example, after preparing the keys for a second display, replace `SECOND_PI_ADDRESS` with its actual address:

```sh
.venv/bin/python -m toronto_postcard.devices --config config.json add \
  --name kitchen-display --host SECOND_PI_ADDRESS --user pi \
  --identity-file .secrets/kitchen-display \
  --known-hosts-file .secrets/kitchen-known-hosts
```

You can also edit `targets` in `config.json` directly. Each entry needs a unique `name`, `host`, `user`, `port`, `identity_file`, and `known_hosts_file`; `enabled` defaults to `true`. All enabled Pis receive the same postcard on the same schedule. One unreachable Pi does not stop delivery to the other devices. After a successful preview:

```sh
.venv/bin/python -m toronto_postcard.publisher --config config.json
```

Adding a Pi needs only its code checkout, working panel driver, restricted public key, pinned host key, and target entry. If another artwork tool also uses the panel, stop the publisher before switching tools. The receiver can share the lock and cooldown with an `eink-frame` helper in the Pi user's home directory.

## Manage devices and the refresh interval

Run these from the project folder on the publisher Mac:

```sh
.venv/bin/python -m toronto_postcard.devices --config config.json list
.venv/bin/python -m toronto_postcard.devices --config config.json disable kitchen-display
.venv/bin/python -m toronto_postcard.devices --config config.json enable kitchen-display
.venv/bin/python -m toronto_postcard.devices --config config.json set-host kitchen-display NEW_PI_ADDRESS
```

Device changes take effect on the next run without reinstalling the service. When an address changes, update that device's pinned known-hosts file using its already verified host key as well. Paused devices receive no connection attempts.

`publish_interval_seconds` in `config.json` controls the schedule; `1800` means 30 minutes. Run `.venv/bin/python scripts/install_mac.py` after changing this value to update launchd. The minimum is 180 seconds to respect the panel's cooldown. `weather_refresh_seconds` controls weather cache reuse and defaults to 1800 in the example configuration.

Additional devices currently means additional Raspberry Pis with this same 600 × 448 Inky panel. A different panel needs a compatible renderer and receiver; registering it alone does not add hardware support.

## Operations

```sh
# Run once or inspect the last status, without displaying private feed credentials
.venv/bin/python -m toronto_postcard.publisher --config config.json
cat .state/status.json
tail -30 ~/Library/Logs/toronto-postcard/publisher.log

# Inspect or stop the scheduled job
launchctl print gui/$(id -u)/local.toronto-postcard.publisher
launchctl bootout gui/$(id -u)/local.toronto-postcard.publisher

# Reinstall/restart after editing settings
.venv/bin/python scripts/install_mac.py

# Verify behavior and export portable version history
.venv/bin/python -m unittest discover -s tests -v
git bundle create toronto-postcard.bundle main
```

The receiver's `.state/last-display.json` confirms the image hash and refresh completion; `.state/palette-preview.png` is the installed driver's pigment preview. Publisher status isolates failures by target and records SSH/receiver errors, so an offline Pi cannot prevent another Pi from updating. Calendar-fetch logs omit credential-bearing exception messages. Mac background logs are in `~/Library/Logs/toronto-postcard/` so launchd can open them when the project is on the Desktop. Log files are private and can be rotated or removed when the job is stopped.

For an explicit manual refresh of an unchanged image, an administrator can run `python3 receiver/receive.py --force < .state/prepared.png` on the Pi. This bypasses only duplicate suppression; the lock, validation, and 180-second cooldown still apply. The restricted publisher key cannot request this flag.

## Version control and updates

The public repository is [palpen/toronto-postcard](https://github.com/palpen/toronto-postcard), with releases on `main`. Keep source changes in commits, rerun the behavior tests, and install matching receiver changes through a trusted administrator connection. Update dependencies deliberately and verify the display after changes. GitHub Actions tests synthetic data only and audits the locked publisher dependencies; it has no access to your calendar or devices.

Publish only the reviewed `main` branch. Local historical refs may contain private deployment details; do not publish all branches, mirror refs, or export private history. A portable bundle of `main` carries the public commit history without private settings. See [SECURITY.md](SECURITY.md) for data handling, connection restrictions, and reporting a vulnerability.
