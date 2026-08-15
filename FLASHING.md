# Flashing a QT Py ESP32-S3 for METARMap

Step-by-step to turn a bare **Adafruit QT Py ESP32-S3 (2MB PSRAM, #5700)** into a
running METARMap. Commands are PowerShell (Windows); adapt paths on Mac/Linux.
Once a board is flashed with these files + libraries + WiFi, it **self-updates
via OTA** — you only do this full process once per board.

## One-time tool setup (per computer)

```
python -m pip install --user esptool circup
```

`esptool` and `circup` install into your Python user Scripts folder, which may
not be on PATH. Either add it to PATH, or call `python -m esptool` and the full
path to `circup.exe`.

Download the CircuitPython firmware (pin the version the project targets):

- Board page: https://circuitpython.org/board/adafruit_qtpy_esp32s3_4mbflash_2mbpsram/
- Direct .bin (10.2.1): https://downloads.circuitpython.org/bin/adafruit_qtpy_esp32s3_4mbflash_2mbpsram/en_US/adafruit-circuitpython-adafruit_qtpy_esp32s3_4mbflash_2mbpsram-en_US-10.2.1.bin

Save it somewhere and use that path below as `$BIN`.

## 1. Put the board in download mode

Native-USB ESP32-S3 needs manual download mode (the double-tap trick is
unreliable — the port vanishes mid-connect):

1. Press and **hold BOOT**.
2. While holding BOOT, **press and release RST**.
3. **Release BOOT**.

It re-appears as a **USB JTAG/serial debug unit** on a new COM port. Find it
(the ESP32-S3 ROM uses USB vendor id `303A`; a running CircuitPython board uses
`239A` — never flash a `239A` port):

```
Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match '\(COM\d+\)' } | ForEach-Object {
  $c=[regex]::Match($_.Name,'\((COM\d+)\)').Groups[1].Value
  $v=[regex]::Match($_.DeviceID,'VID_([0-9A-F]{4})').Groups[1].Value
  if($v -eq '303A'){ "DOWNLOAD MODE: $c" }
}
```

## 2. Flash CircuitPython

Replace `COMx` with the port from step 1:

```
python -m esptool --port COMx --chip esp32s3 erase-flash
python -m esptool --port COMx --chip esp32s3 write-flash 0x0 $BIN
```

Wait for `Hash of data verified.`

## 3. Mount CIRCUITPY

Press **RST** once. A `CIRCUITPY` drive appears (usually `D:`). If it doesn't,
press RST again or unplug/replug USB.

## 4. Install the libraries

```
circup install neopixel adafruit_requests adafruit_connection_manager adafruit_httpserver adafruit_ntp adafruit_hashlib adafruit_ssd1327 adafruit_display_text adafruit_ili9341
```

(circup pulls dependencies automatically: `adafruit_pixelbuf`, `adafruit_bitmap_font`,
`adafruit_ticks`, `adafruit_connection_manager`.) The `ssd1327`/`ili9341`/
`display_text` libs are only needed if you attach a display, but installing them
makes the board ready for either.

## 5. Copy the device files

Copy these 10 files from the repo to the `CIRCUITPY` root (`code.py` **last**, so
auto-reload doesn't run it before its imports exist):

```
boot.py  metar_source.py  render.py  updater.py  wifi_setup.py
captive_dns.py  webui.py  display.py  version.json          (then) code.py
```

No font file is needed (the display uses the built-in font).

## 6. WiFi setup — power it from a wall charger, NOT the computer

**Unplug the board from the computer and power it from a USB wall charger or
battery**, then:

1. On a phone, join the open **`METARMap-Setup`** network.
2. The setup page should pop up automatically (or browse to `http://192.168.4.1`).
3. Pick your WiFi, enter the password, Save. It reboots and joins your network.

> **Why wall power matters:** right after copying files the board runs in a
> "soft-reload" state where the *computer* still owns the flash, so CircuitPython
> can't write `config.json` and Save fails. A real power-on runs `boot.py`, which
> hands the flash to the board. (Newer firmware shows a clear message if you try
> to save while tethered.)

## 7. Configure in the browser

Once online, open the board's IP (printed to serial) or `http://<name>.local`:

- **Airports** — your ICAO list in LED order; `NULL` lines = skipped/gap LEDs.
- **LED count** and **LED color order** (`GRB` for WS2812B & SK6812 RGB, `GRBW`
  for RGBW strips).
- **Display type** — `auto` (OLED then TFT), or `oled`/`tft`/`off`.
- **Board name** — set a **unique** name per board (`metarmap1`, `metarmap2`, …)
  so `*.local` doesn't collide when several are on the network.
- Timezone (`tzOffsetHours`), dimming, weather-effect toggles, etc.

## Hardware notes

- **LED data pin is A3** (the NeoPixel Driver BFF #5645 default).
- **OLED** (SSD1327, #4741): plugs into STEMMA QT (I2C), no soldering.
- **TFT** (ILI9341, e.g. SparkFun COM-28380): SPI — SCK→SCK, MOSI→MOSI, CS→A0,
  DC→A1, RESET→A2, backlight→3V, VCC→3V, GND→GND. LEDs stay on A3.
- Many LEDs draw real current; inject 5V into the strip rather than powering it
  all through the QT Py's USB.

## Troubleshooting

- **Nothing lights:** confirm LED data is on **A3**; check `ledOrder`; the board
  only lights as many LEDs as you have airport lines.
- **Save fails / "connection lost" in the captive portal:** the board is tethered
  to a computer — power it from a wall charger and retry (see step 6).
- **`*.local` doesn't resolve / two boards clash:** give each a unique Board name.
- **Display says "TFT ready" with nothing attached:** harmless — SPI can't be
  probed, so `auto` assumes TFT when no OLED is found. Set `display_type=off` for
  a screenless board; an OLED takes priority when present.
- **Need to install more libraries later** (flash is read-only while running):
  use the **USB maintenance** button in the web UI, which reboots with the drive
  writable so you can `circup` from a computer; press RST when done.
- **After a fresh flash the board self-updates** to the latest firmware on GitHub
  once it's online — no need to re-copy files for updates.
