# code.py — main entry point on the QT Py ESP32-S3 (#5700).
#
# Orchestrates the whole map: WiFi (or setup portal) -> NTP clock -> weather
# fetch -> LED render (with time-of-day dimming / off) -> config web UI ->
# periodic OTA.
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


def make_net():
    # One socket pool shared by the HTTPS client (weather + OTA) and the config
    # web server, so we don't exhaust sockets.
    import socketpool
    import ssl
    import wifi
    import adafruit_requests
    pool = socketpool.SocketPool(wifi.radio)
    session = adafruit_requests.Session(pool, ssl.create_default_context())
    return pool, session


def sync_clock(pool):
    # Set the RTC from NTP in UTC, so epoch math (data staleness) stays correct.
    # Local time for dimming/off is derived separately from tzOffsetHours.
    try:
        import adafruit_ntp
        import rtc
        ntp = adafruit_ntp.NTP(pool, tz_offset=0, cache_seconds=3600)
        rtc.RTC().datetime = ntp.datetime
        print("code.py: clock synced (UTC)")
        return True
    except Exception as e:
        print("code.py: NTP sync failed:", e)
        return False


def local_hour(now):
    # now is a UTC epoch; shift by the configured offset to get the local hour.
    tz = CONFIG.get("tzOffsetHours", 0)
    return time.localtime(int(now + tz * 3600)).tm_hour


def _in_window(hour, start, end):
    # True if `hour` is within [start, end), wrapping past midnight if start > end.
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


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
    pool, session = make_net()
    sync_clock(pool)

    # mDNS: also reach the map at http://<name>.local (default metarmap.local),
    # so users don't need to know the DHCP-assigned IP. Keep a reference so it
    # isn't garbage-collected. Works on iOS/macOS/Windows/Linux; harmless if not.
    try:
        import wifi
        import mdns
        _mdns = mdns.Server(wifi.radio)
        _mdns.hostname = CONFIG.get("hostname", "metarmap")
        _mdns.advertise_service(service_type="_http", protocol="_tcp", port=80)
        print("code.py: mDNS -> http://%s.local" % _mdns.hostname)
    except Exception as e:
        print("code.py: mDNS unavailable:", e)

    # Config web UI, served alongside the render loop on the map's own IP.
    ui = None
    server = None
    try:
        import webui
        from adafruit_httpserver import Server
        import wifi
        server = Server(pool, debug=False)
        ui = webui.ConfigUI(CONFIG, renderer, pixels, session)
        ui.register(server)
        server.start(str(wifi.radio.ipv4_address), port=80)   # plain http:// (no :5000)
        print("code.py: config UI at http://%s" % wifi.radio.ipv4_address)
    except Exception as e:
        print("code.py: config UI unavailable:", e)

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

        # Time-of-day: a configured "off" window blanks the strip entirely;
        # otherwise apply day/night dimming, then animate. Values are read live
        # so UI changes take effect without a reboot.
        lh = local_hour(now)
        map_off = (CONFIG.get("offEnabled", False)
                   and _in_window(lh, CONFIG.get("offHour", 22), CONFIG.get("onHour", 7)))
        if map_off:
            pixels.fill((0, 0, 0))
            pixels.show()
        else:
            if CONFIG.get("dimming_enabled", False):
                daytime = _in_window(lh, CONFIG.get("brightHour", 7), CONFIG.get("dimHour", 19))
                pixels.brightness = (CONFIG.get("ledBrightness", 0.5) if daytime
                                     else CONFIG.get("ledBrightnessDim", 0.1))
            renderer.render_frame(conditions)

        # Serve the config UI without blocking the animation (even while "off").
        if server is not None:
            try:
                server.poll()
            except Exception as e:
                print("code.py: UI poll error:", e)
            ui.tick()

        # Daily jobs at the configured local hour: refresh the clock, check OTA.
        day = time.localtime(now).tm_yday
        if lh == UPDATE_HOUR and day != last_update_day:
            last_update_day = day
            sync_clock(pool)
            if AUTO_UPDATE:
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
