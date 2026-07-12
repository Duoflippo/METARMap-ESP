# METARMap-ESP

A live aviation-weather map: SK6812 RGB LEDs show each airport's flight category
(VFR / MVFR / IFR / LIFR) with animations for wind, thunderstorms, freezing
precipitation, snow, fog, severe weather, icing potential, and stale data.

Runs on an **Adafruit QT Py ESP32-S2** (#5325) driving the LED string through an
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
| `render.py` | *(TODO)* precedence engine + LED animations |
| `wifi_setup.py` | *(TODO)* first-run WiFi provisioning (AP mode) |
| `webui.py` | *(TODO)* on-device config web UI |
| `config.json` | *(device-only, not in git)* user settings + WiFi creds |
| `version.json` | OTA manifest: current version + file hashes |

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
