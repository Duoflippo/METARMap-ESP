# Flashing a New QT Py S3 — No Computer-Side Coding Needed

This is CircuitPython, not Arduino — there's nothing to compile. Setting up a
new board is firmware install + copying files. Do this once per board; no
Claude required.

## One-time setup (on the computer you'll use for flashing)

1. Install Python 3.10+ if you don't have it.
2. `pip install circup`

## Per-board steps

**1. Put the board in bootloader mode and install CircuitPython**

- Plug the QT Py S3 in via USB-C.
- Double-tap the reset button — a "slow double-click": tap once, wait for the
  RGB LED to turn purple (~0.5s), tap again.
- A drive named `QTPYS3BOOT` appears.
- Get the current CircuitPython `.uf2` for this exact board (4MB Flash / 2MB
  PSRAM) from
  https://circuitpython.org/board/adafruit_qtpy_esp32s3_4mbflash_2mbpsram/
- Drag the `.uf2` file onto `QTPYS3BOOT`. The board reboots on its own and a
  `CIRCUITPY` drive appears — that's the board's filesystem.

**2. Install the required libraries**

With the board plugged in (`CIRCUITPY` visible), from this project folder run:

```
circup install -r tools/requirements.txt
```

circup copies the matching `.mpy` library files into `lib/` on the board
for you — matched to whatever CircuitPython version you just flashed.

**3. Copy the project code onto the board**

Copy these files from this folder onto the `CIRCUITPY` drive (root level,
overwrite if asked):

```
boot.py  code.py  metar_source.py  updater.py  render.py
wifi_setup.py  captive_dns.py  webui.py  display.py
version.json  LeagueSpartan-Bold-16.bdf
```

Do **not** copy `config.json`, `secrets.py`, or `wifi.json` — those are
device-specific and get created fresh on first boot.

**4. Eject and power up**

Safely eject `CIRCUITPY`, unplug, then power the board normally. On first
boot it broadcasts the `METARMap-Setup` WiFi network for provisioning — see
the main README's "First-run setup" section.

## Speeding up a production run (many boards)

Steps 1–2 (firmware + circup) are the slow part. Once you've done them on one
board, you can skip circup for the rest of the batch: just drag-and-drop that
board's entire `lib/` folder onto each new board's `CIRCUITPY` drive after
flashing CircuitPython (step 1). Then do step 3 as normal. Plain file copies
are much faster than re-resolving libraries with circup each time, and it
still works offline.

Keep a known-good `lib/` folder (and the `.uf2` file) saved locally so you're
not re-downloading from the internet for every unit.
