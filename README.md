# METARMap-ESP

A live aviation-weather map: SK6812 RGB LEDs show each airport's flight category
(VFR / MVFR / IFR / LIFR) with animations for wind, thunderstorms, freezing
precipitation, snow, fog, severe weather, icing potential, and stale data.

Runs on an **Adafruit QT Py ESP32-S3** (#5700) driving the LED string through an
**Adafruit NeoPixel Driver BFF** (#5645). Data comes from the current
aviationweather.gov API (`/api/data/metar`, JSON).

## Why this exists / what's different

- **Self-updating.** Devices check this repo nightly and install new code
  over WiFi (OTA). When aviationweather.gov changes their API, one fix here
  heals every deployed map overnight — no re-flashing.
- **API code is isolated.** Everything provider-specific lives in
  `metar_source.py`, so an API change touches exactly one small file.
- **User settings are safe.** WiFi credentials and `config.json` live only on
  the device and are `.gitignore`'d. OTA only ever overwrites code.

## Files on the device

| File | Role |
|---|---|
| `boot.py` | Makes flash writable; rolls back a bad OTA update |
| `code.py` | Main loop: WiFi → fetch → render → periodic OTA check |
| `metar_source.py` | The ONLY file that talks to aviationweather.gov |
| `updater.py` | OTA: fetch manifest → verify → install → reboot |
| `render.py` | Precedence engine + LED animations |
| `wifi_setup.py` | First-run WiFi provisioning (AP mode) |
| `webui.py` | On-device config web UI (weather toggles, dimming, etc.) |
| `config.json` | *(device-only, not in git)* user settings + WiFi creds |
| `version.json` | OTA manifest: current version + file hashes |

## CircuitPython libraries required

Install the current CircuitPython build on the QT Py, then `circup install`:

- `neopixel`
- `adafruit_requests`
- `adafruit_connection_manager`
- `adafruit_httpserver`
- `adafruit_ntp` *(clock sync for time-based dimming / OTA scheduling)*
- `adafruit_hashlib` *(optional — enables SHA-256 verification of OTA downloads)*

## First-run setup (no computer needed)

1. Power on the map. With no WiFi saved, it broadcasts an open network named
   **`METARMap-Setup`**.
2. Join that network from a phone and open **`http://192.168.4.1`**.
3. Pick your WiFi from the scanned list, enter the password, tap Save.
4. The map reboots and connects. If it ever can't (e.g. you change routers),
   `METARMap-Setup` reappears automatically so you can reconfigure.

## Configuring the map (in a browser)

Once online, the map serves a config page at its own IP (printed to the serial
console at boot, e.g. `http://192.168.1.42`). From there you can set the airport
list, toggle each weather effect, tune wind thresholds and brightness/dimming,
check for updates, reboot, or switch WiFi networks — no computer or re-flash
needed. Weather and brightness changes apply instantly; airport/LED-count
changes take effect after a reboot.

## Cutting a release (OTA)

1. Edit device code.
2. Regenerate the manifest with a bumped version:
   ```
   python tools/make_manifest.py 2.0.1
   ```
3. Commit + push `version.json` and the changed files to `main`.
4. Deployed devices pick it up on their next nightly check (default 03:00
   local), verify the hashes, install, and reboot. boot.py rolls back
   automatically if the new code fails to run.

## Local testing (no hardware needed)

`metar_source.py` runs on desktop Python against the live API:

```
python metar_source.py KSEA KJFK KORD
```
