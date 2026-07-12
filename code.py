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

# --- Config -----------------------------------------------------------------
# All user-editable settings live in config.json on the device (never in git).
# Until the config UI exists, missing values fall back to these defaults.
try:
    import json
    with open("config.json") as f:
        CONFIG = json.load(f)
except (OSError, ValueError):
    CONFIG = {}

AIRPORTS    = CONFIG.get("airports", ["KSEA", "KJFK", "KORD"])
REFRESH_MIN = CONFIG.get("refreshMinutes", 5)
AUTO_UPDATE = CONFIG.get("autoUpdate", True)
UPDATE_HOUR = CONFIG.get("autoUpdateHour", 3)   # local hour to check for OTA


def connect_wifi():
    # TODO: replace with wifi_setup.connect() — stored creds + AP provisioning fallback.
    import wifi
    import os
    ssid = CONFIG.get("wifiSsid") or os.getenv("CIRCUITPY_WIFI_SSID")
    password = CONFIG.get("wifiPassword") or os.getenv("CIRCUITPY_WIFI_PASSWORD")
    if not ssid:
        print("code.py: no WiFi creds — provisioning UI is TODO (wifi_setup.py)")
        return False
    print("code.py: connecting to %s ..." % ssid)
    wifi.radio.connect(ssid, password)
    print("code.py: connected, IP =", wifi.radio.ipv4_address)
    return True


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


def render(conditions):
    # TODO: render.py — precedence engine + LED animations
    # (severe > freezing > thunderstorm > high wind > snow > base category).
    for sid in AIRPORTS:
        c = conditions.get(sid)
        print("  %-5s %s" % (sid, c["flightCategory"] if c else "None"))


def main():
    if not connect_wifi():
        return
    session = make_session()
    sync_clock(session)

    # Check for updates on boot (also runs daily in the loop below).
    if AUTO_UPDATE:
        updater.check_and_update(session)   # reboots if an update installs

    last_refresh = 0
    last_update_day = -1
    healthy_confirmed = False

    while True:
        now = time.time()

        if now - last_refresh >= REFRESH_MIN * 60:
            try:
                conditions = metar_source.get_conditions(session, AIRPORTS, now_epoch=int(now))
                render(conditions)
                last_refresh = now
                if not healthy_confirmed:
                    updater.confirm_healthy()   # accept any pending OTA update
                    healthy_confirmed = True
            except Exception as e:
                print("code.py: refresh error:", e)

        if AUTO_UPDATE:
            t = time.localtime(now)
            if t.tm_hour == UPDATE_HOUR and t.tm_yday != last_update_day:
                last_update_day = t.tm_yday
                updater.check_and_update(session)

        time.sleep(5)


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
