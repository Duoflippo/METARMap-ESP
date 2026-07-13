# webui.py — on-device configuration UI, served over the user's WiFi.
#
# Runs ALONGSIDE the render loop: code.py calls server.poll() and ui.tick()
# every frame, so the LEDs keep animating while the page is open. Weather and
# brightness changes apply live; structural changes (airports, LED count) take
# effect on the next reboot (there's a Reboot button).
#
# Reuses the same adafruit_httpserver stack proven out in wifi_setup.py.

import time
import render

# Non-weather defaults (weather defaults come from render.DEFAULTS).
DEFAULTS_EXTRA = {
    "airports": ["KSEA", "KJFK", "KORD"],
    "ledCount": 50,
    "refreshMinutes": 5,
    "ledBrightness": 0.5,
    "ledBrightnessDim": 0.1,
    "dimming_enabled": False,
    "brightHour": 7,
    "dimHour": 19,
    "offEnabled": False,
    "offHour": 22,
    "onHour": 7,
    "tzOffsetHours": 0,
    "autoUpdate": True,
    "autoUpdateHour": 3,
    "hostname": "metarmap",
    "display_enabled": True,
    "display_rotation_secs": 5,
    "display_airports": [],
}

# The form layout. Each field: (key, label, type). type drives render + parse.
SCHEMA = [
    ("Airports & refresh", [
        ("airports", "Airports (one ICAO per line; NULL = gap LED)", "airports"),
        ("ledCount", "Total LED count", "int"),
        ("refreshMinutes", "Weather refresh interval (minutes)", "int"),
    ]),
    ("Weather effects", [
        ("lightning_enabled", "Thunderstorm - white flash", "bool"),
        ("freezing_enabled", "Freezing precip - rose flash", "bool"),
        ("snow_enabled", "Snow - white twinkle", "bool"),
        ("severe_enabled", "Severe (tornado/hail/squall) - strobe", "bool"),
        ("wind_enabled", "Wind - fade/blink + high-wind yellow", "bool"),
        ("fog_enabled", "Fog - slow breathe", "bool"),
        ("rain_enabled", "Heavy rain - blue shimmer", "bool"),
        ("icing_enabled", "Icing potential - cyan tint", "bool"),
        ("stale_enabled", "Stale data - dim pulse", "bool"),
    ]),
    ("Wind tuning", [
        ("wind_threshold", "Wind blink/fade threshold (kt)", "int"),
        ("high_wind_threshold", "High-wind yellow threshold (kt, -1 off)", "int"),
        ("fade_instead_of_blink", "Fade instead of blink", "bool"),
        ("always_blink_gusts", "Always animate for gusts", "bool"),
        ("blink_speed", "Blink/fade half-cycle (seconds)", "float"),
    ]),
    ("Brightness & dimming", [
        ("ledBrightness", "Daytime brightness (0.0-1.0)", "float"),
        ("ledBrightnessDim", "Night brightness (0.0-1.0)", "float"),
        ("dimming_enabled", "Enable night dimming", "bool"),
        ("brightHour", "Bright starts at hour (0-23)", "hour"),
        ("dimHour", "Dim starts at hour (0-23)", "hour"),
        ("offEnabled", "Turn map fully OFF overnight", "bool"),
        ("offHour", "Off starts at hour (0-23)", "hour"),
        ("onHour", "On again at hour (0-23)", "hour"),
    ]),
    ("Display (optional OLED)", [
        ("display_enabled", "Enable OLED display", "bool"),
        ("display_rotation_secs", "Seconds per airport", "float"),
        ("display_airports", "Airports to show (blank = all; ICAO per line)", "airportlist"),
    ]),
    ("System", [
        ("hostname", "Board name for x.local (reboot to apply)", "text"),
        ("tzOffsetHours", "UTC offset in hours (e.g. -7; no auto-DST)", "float"),
        ("autoUpdate", "Auto-update from GitHub", "bool"),
        ("autoUpdateHour", "Daily update-check hour (0-23)", "hour"),
    ]),
]


class ConfigUI:
    def __init__(self, config, renderer, pixels, session, config_path="config.json"):
        self.config = config
        self.renderer = renderer
        self.pixels = pixels
        self.session = session
        self.config_path = config_path
        self._pending = None   # (action, run_at) executed by tick()

    # --- lifecycle ----------------------------------------------------------

    def register(self, server):
        from adafruit_httpserver import Response, POST

        @server.route("/", "GET")
        def _index(request):
            return Response(request, self._page(), content_type="text/html")

        @server.route("/save", POST)
        def _save(request):
            self._parse_and_save(_parse_form(request.body))
            return Response(request, _msg("Saved", "Settings applied.", back=True),
                            content_type="text/html")

        @server.route("/update", POST)
        def _update(request):
            self._pending = ("update", time.monotonic() + 2)
            return Response(request, _msg("Checking for updates",
                            "If a newer version exists it will install and reboot."),
                            content_type="text/html")

        @server.route("/wifi", POST)
        def _wifi(request):
            # Forget stored creds so the next boot re-enters the setup portal.
            self.config["wifiSsid"] = ""
            self.config["wifiPassword"] = ""
            self._save()
            self._pending = ("reboot", time.monotonic() + 2)
            return Response(request, _msg("Changing WiFi",
                            "Rebooting into the METARMap-Setup portal."),
                            content_type="text/html")

        @server.route("/reboot", POST)
        def _reboot(request):
            self._pending = ("reboot", time.monotonic() + 2)
            return Response(request, _msg("Rebooting", "The map will restart now."),
                            content_type="text/html")

    def tick(self):
        """Called each frame from code.py to run any deferred action after its
        HTTP response has had time to flush."""
        if not self._pending:
            return
        action, run_at = self._pending
        if time.monotonic() < run_at:
            return
        self._pending = None
        if action == "reboot":
            import microcontroller
            microcontroller.reset()
        elif action == "update":
            import updater
            updater.check_and_update(self.session)   # reboots if it installs

    # --- values -------------------------------------------------------------

    def _default(self, key):
        if key in render.DEFAULTS:
            return render.DEFAULTS[key]
        return DEFAULTS_EXTRA.get(key)

    def current(self, key):
        return self.config.get(key, self._default(key))

    def _save(self):
        import json
        try:
            with open(self.config_path, "w") as f:
                json.dump(self.config, f)
        except OSError as e:
            print("webui: could not save config:", e)

    def _parse_and_save(self, form):
        for _, fields in SCHEMA:
            for key, _label, typ in fields:
                if typ == "bool":
                    self.config[key] = key in form          # checkbox present == on
                elif typ in ("int", "hour"):
                    try:
                        self.config[key] = int(form.get(key, self.current(key)))
                    except (ValueError, TypeError):
                        pass
                elif typ == "float":
                    try:
                        self.config[key] = float(form.get(key, self.current(key)))
                    except (ValueError, TypeError):
                        pass
                elif typ == "text":
                    v = form.get(key, "").strip()
                    if v:
                        self.config[key] = v
                elif typ == "airports":
                    raw = form.get(key, "")
                    aps = [tok.strip().upper() for tok in raw.replace(",", " ").split()]
                    if aps:
                        self.config[key] = aps          # never wipe the main map list
                elif typ == "airportlist":
                    raw = form.get(key, "")
                    # empty is valid here -> "show all airports"
                    self.config[key] = [tok.strip().upper() for tok in raw.replace(",", " ").split()]
        self._save()
        self._apply_live()

    def _apply_live(self):
        # Weather + wind settings take effect immediately (renderer reads cfg
        # every frame). Airports / LED count need a reboot.
        weather = {k: self.config[k] for k in render.DEFAULTS if k in self.config}
        self.renderer.cfg.update(weather)
        try:
            self.pixels.brightness = float(self.current("ledBrightness"))
        except (ValueError, TypeError):
            pass

    # --- HTML ---------------------------------------------------------------

    def _version(self):
        try:
            import json
            with open("version.json") as f:
                return json.load(f).get("version", "?")
        except (OSError, ValueError):
            return "?"

    def _field_html(self, key, label, typ):
        val = self.current(key)
        if typ == "bool":
            chk = "checked" if val else ""
            return ("<label class='cb'><input type='checkbox' name='%s' %s>%s</label>"
                    % (key, chk, label))
        if typ in ("airports", "airportlist"):
            rows = "8" if typ == "airports" else "4"
            text = "\n".join(val if isinstance(val, list) else [])
            return ("<label>%s</label><textarea name='%s' rows='%s'>%s</textarea>"
                    % (label, key, rows, text))
        if typ == "text":
            return ("<label>%s</label><input type='text' name='%s' value='%s'>"
                    % (label, key, val))
        if typ in ("int", "hour"):
            extra = "min='0' max='23'" if typ == "hour" else ""
            return ("<label>%s</label><input type='number' name='%s' value='%s' %s>"
                    % (label, key, val, extra))
        if typ == "float":
            return ("<label>%s</label><input type='number' step='0.05' name='%s' value='%s'>"
                    % (label, key, val))
        return ""

    def _page(self):
        parts = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' "
            "content='width=device-width,initial-scale=1'><title>METARMap</title>",
            "<style>body{font-family:sans-serif;max-width:520px;margin:1.5em auto;"
            "padding:0 1em}fieldset{border:1px solid #ccc;border-radius:8px;margin:1em 0}"
            "legend{font-weight:bold}label{display:block;margin:.7em 0 .2em}"
            "label.cb{display:flex;gap:.5em;align-items:center;margin:.5em 0}"
            "label.cb input{width:auto}input,textarea,select{width:100%;padding:.5em;"
            "box-sizing:border-box;font-size:1em}button{padding:.7em 1em;font-size:1em;"
            "margin:.3em .3em 0 0}.actions{display:flex;flex-wrap:wrap;margin-top:1em}"
            ".save{width:100%;background:#1663a6;color:#fff;border:0;border-radius:6px;"
            "padding:.9em;font-size:1.05em}</style></head><body>",
            "<h1>METARMap</h1><p style='color:#666'>firmware v%s</p>" % self._version(),
            "<form action='/save' method='post'>",
        ]
        for title, fields in SCHEMA:
            parts.append("<fieldset><legend>%s</legend>" % title)
            for key, label, typ in fields:
                parts.append(self._field_html(key, label, typ))
            parts.append("</fieldset>")
        parts.append("<button class='save' type='submit'>Save settings</button></form>")
        # Separate action forms (each POSTs its own endpoint).
        parts.append(
            "<div class='actions'>"
            "<form action='/update' method='post'><button>Check for updates</button></form>"
            "<form action='/reboot' method='post'><button>Reboot</button></form>"
            "<form action='/wifi' method='post'><button>Change WiFi</button></form>"
            "</div>")
        parts.append("</body></html>")
        return "".join(parts)


# --- form parsing (self-contained so OTA can update this file independently) --

def _urldecode(s):
    s = s.replace("+", " ")
    out = ""
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            try:
                out += chr(int(s[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out += s[i]
        i += 1
    return out


def _parse_form(body):
    data = {}
    try:
        text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)
    except Exception:
        text = str(body)
    for pair in text.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            data[_urldecode(k)] = _urldecode(v)
    return data


def _msg(title, body, back=False):
    link = "<p><a href='/'>&larr; Back to settings</a></p>" if back else ""
    return ("<!DOCTYPE html><html><head><meta name='viewport' "
            "content='width=device-width,initial-scale=1'>"
            "<style>body{font-family:sans-serif;max-width:520px;margin:2em auto;"
            "padding:0 1em}</style></head><body><h1>%s</h1><p>%s</p>%s</body></html>"
            % (title, body, link))
