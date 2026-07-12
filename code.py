# code.py — main entry point on the QT Py ESP32-S2.
#
# Orchestrates the whole map. This is a SKELETON: sections marked TODO are
# stubs until their module is built (wifi_setup, render, webui). It already
# wires up the two finished pieces — metar_source (data) and updater (OTA).
#
# boot.py has already run before this (flash made writable + rollback check).

import time
import metar_source
import updater
import render

# How often to repaint the strip. ~12 fps keeps fades/twinkles smooth.
FRAME_INTERVAL = 0.08

# --- Config -----------------------------------------------------------------
# All user-editable settings live in config.json on the device (never in git).
# Until the config UI exists, missing values fall back to these defaults.
try:
    import json
    with open("config.json") as f:
        CONFIG = json.load(f)
except (OSError, ValueError):
    CONFIG = {}

AIRPORTS       = CONFIG.get("airports", ["KSEA", "KJFK", "KORD"])
REFRESH_MIN    = CONFIG.get("refreshMinutes", 5)
AUTO_UPDATE    = CONFIG.get("autoUpdate", True)
UPDATE_HOUR    = CONFIG.get("autoUpdateHour", 3)   # local hour to check for OTA
LED_COUNT      = CONFIG.get("ledCount", 50)
LED_BRIGHTNESS = CONFIG.get("ledBrightness", 0.5)  # 0.0-1.0 (day/night dimming: TODO)


def connect_wifi(pixels):
    # Connect with stored creds, or fall back to the AP setup portal. If no
    # credentials work, ensure_connected serves the portal and reboots when the
    # user saves — so this only returns True once we're actually online.
    import wifi_setup
    return wifi_setup.ensure_connected(CONFIG, pixels=pixels)


def make_session():
    import socketpool
    import ssl
    import wifi
    import adafruit_requests
    pool = socketpool.SocketPool(wifi.radio)
    return adafruit_requests.Session(pool, ssl.create_default_context())


def sync_clock(session):
    # TODO: NTP sync (adafruit_ntp) so time.localtime() is correct for
    # daytime dimming and the daily OTA schedule.
    pass


def make_pixels():
    import board
    import neopixel
    # The NeoPixel Driver BFF (#5645) drives the strip off pin A0.
    return neopixel.NeoPixel(
        board.A0, LED_COUNT,
        brightness=LED_BRIGHTNESS,
        pixel_order=neopixel.GRB,   # SK6812 RGB
        auto_write=False,
    )


def main():
    pixels = make_pixels()
    renderer = render.Renderer(pixels, AIRPORTS, CONFIG)

    if not connect_wifi(pixels):
        return   # unreachable: portal reboots the device once WiFi is configured
    pixels.brightness = LED_BRIGHTNESS   # restore after any setup-mode indicator
    session = make_session()
    sync_clock(session)

    # Check for updates on boot (also runs daily in the loop below).
    if AUTO_UPDATE:
        updater.check_and_update(session)   # reboots if an update installs

    conditions = {}
    last_refresh = 0
    last_update_day = -1
    healthy_confirmed = False

    while True:
        now = time.time()

        # Fetch fresh weather every REFRESH_MIN; animate continuously in between.
        if now - last_refresh >= REFRESH_MIN * 60:
            try:
                conditions = metar_source.get_conditions(session, AIRPORTS, now_epoch=int(now))
                last_refresh = now
                if not healthy_confirmed:
                    updater.confirm_healthy()   # accept any pending OTA update
                    healthy_confirmed = True
            except Exception as e:
                print("code.py: refresh error:", e)

        renderer.render_frame(conditions)

        if AUTO_UPDATE:
            t = time.localtime(now)
            if t.tm_hour == UPDATE_HOUR and t.tm_yday != last_update_day:
                last_update_day = t.tm_yday
                updater.check_and_update(session)

        time.sleep(FRAME_INTERVAL)


try:
    main()
except Exception as e:
    # Any unhandled crash: record it and reset, so boot.py's rollback watchdog
    # can count failed boots and restore a previous version if an OTA broke it.
    import microcontroller
    print("code.py: fatal:", e)
    try:
        microcontroller.nvm[2] = min(microcontroller.nvm[2] + 1, 255)
    except Exception:
        pass
    time.sleep(3)
    microcontroller.reset()
