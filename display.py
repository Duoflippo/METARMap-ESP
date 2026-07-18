# display.py — optional I2C OLED that rotates through METAR conditions.
#
# Target: Adafruit 1.5" 128x128 grayscale OLED, SSD1327 driver, STEMMA QT (#4741).
# Plugs into the QT Py's STEMMA QT port (I2C) with no soldering, independent of
# the LED data pin (A0). Auto-disables cleanly if the display OR its libraries
# are absent, so shipping this module to a board without them is harmless.
#
# Layout (128x128):
#   Row 1 (big, scale 2):  ICAO ......... CATEGORY   (opposite corners)
#   Body (scale 1):        Wind / [wx] / Vis / cloud layers / Temp-Dewpoint
#   (observation time is intentionally dropped to make room for clouds)
#
# format_lines() is pure (no hardware) so it can be unit-tested on desktop.


def _cloud_str(layer):
    """A METAR-style cloud layer like 'BKN014' (cover + base in hundreds of ft)."""
    cover = (layer.get("cover") or "").upper()
    base = layer.get("base")
    if base is not None and cover and cover not in ("CLR", "SKC", "NSC", "NCD"):
        return "%s%03d" % (cover, int(base) // 100)
    return cover or "SKC"


def format_lines(sid, c):
    """Return (icao, category, [body lines]) for one station."""
    if c is None:
        return sid, "", ["(no data)"]

    cat = c.get("flightCategory") or "?"

    wdir = c.get("windDir")
    spd = c.get("windSpeed", 0)
    gust = c.get("windGustSpeed", 0)
    if spd == 0 and not gust:
        wind = "calm"
    else:
        wind = "%s@%d" % ("VRB" if wdir is None else wdir, spd)
        if gust:
            wind += "G%d" % gust
    body = ["Wind " + wind]

    wx = (c.get("wxString") or "").strip()
    if wx:
        body.append(wx)

    vis = c.get("visibility")
    body.append(("Vis %gSM" % vis) if vis is not None else "Vis ?")

    clouds = c.get("clouds") or []
    if clouds:
        for layer in clouds:
            body.append(_cloud_str(layer))
    else:
        body.append("CLR")

    t = c.get("tempC")
    d = c.get("dewpointC")
    if t is not None or d is not None:
        body.append("T/Dp %s/%sC" % (t if t is not None else "?", d if d is not None else "?"))

    return sid, cat, body


class MetarDisplay:
    ADDRS = (0x3D, 0x3C)   # SSD1327 default 0x3D, some boards 0x3C

    def __init__(self, rotation_secs=5.0):
        self.rotation_secs = rotation_secs
        self.ok = False
        self._airports = []
        self._idx = 0
        self._last = 0.0
        try:
            self._setup()
            self.ok = True
            print("display: OLED ready")
        except Exception as e:
            print("display: no OLED / init skipped:", e)

    def _setup(self):
        import board
        import displayio
        import terminalio
        import adafruit_ssd1327          # SSD1327 driver (note: no displayio_ prefix)
        from adafruit_display_text import label
        try:
            from i2cdisplaybus import I2CDisplayBus       # CircuitPython 9+
        except ImportError:
            from displayio import I2CDisplay as I2CDisplayBus

        displayio.release_displays()
        try:
            i2c = board.STEMMA_I2C()
        except AttributeError:
            i2c = board.I2C()

        bus = None
        last_err = None
        for addr in self.ADDRS:
            try:
                bus = I2CDisplayBus(i2c, device_address=addr)
                break
            except Exception as e:
                last_err = e
        if bus is None:
            raise last_err or RuntimeError("no display on I2C")

        self.display = adafruit_ssd1327.SSD1327(bus, width=128, height=128)
        self.group = displayio.Group()
        try:
            self.display.root_group = self.group
        except AttributeError:
            self.display.show(self.group)                 # older displayio

        # Header row: ICAO top-left + category top-right, both big (scale 2).
        self._icao = label.Label(terminalio.FONT, text="", scale=2,
                                 anchor_point=(0.0, 0.0), anchored_position=(2, 2))
        self._cat = label.Label(terminalio.FONT, text="", scale=2,
                                anchor_point=(1.0, 0.0), anchored_position=(126, 2))
        self.group.append(self._icao)
        self.group.append(self._cat)

        # Body: up to 8 detail lines at scale 1, below the header.
        self._body = [label.Label(terminalio.FONT, text="",
                                  anchor_point=(0.0, 0.0), anchored_position=(2, 30 + i * 12))
                      for i in range(8)]
        for lbl in self._body:
            self.group.append(lbl)

    def set_airports(self, airports):
        """The ordered list to rotate through (NULL placeholders removed)."""
        self._airports = [a for a in (airports or []) if a and a != "NULL"]
        if self._idx >= len(self._airports):
            self._idx = 0

    def tick(self, conditions, now):
        if not self.ok or not self._airports:
            return
        if now - self._last < self.rotation_secs:
            return
        self._last = now
        sid = self._airports[self._idx]
        self._idx = (self._idx + 1) % len(self._airports)
        icao, cat, body = format_lines(sid, (conditions or {}).get(sid))
        self._icao.text = icao
        self._cat.text = cat
        for i, lbl in enumerate(self._body):
            lbl.text = body[i] if i < len(body) else ""


# --- desktop self-test: `python display.py` ---------------------------------
if __name__ == "__main__":
    tests = [
        ("KSEA", {"flightCategory": "VFR", "windDir": 270, "windSpeed": 6,
                  "windGustSpeed": 0, "visibility": 10.0, "tempC": 19, "dewpointC": 7,
                  "wxString": "", "clouds": [{"cover": "FEW", "base": 3000}]}),
        ("KJFK", {"flightCategory": "IFR", "windDir": None, "windSpeed": 12,
                  "windGustSpeed": 20, "visibility": 2.0, "tempC": 3, "dewpointC": 2,
                  "wxString": "-RA BR", "clouds": [{"cover": "SCT", "base": 1400},
                  {"cover": "BKN", "base": 2500}, {"cover": "OVC", "base": 4000}]}),
        ("KBFI", {"flightCategory": "VFR", "windDir": 0, "windSpeed": 0,
                  "windGustSpeed": 0, "visibility": 10.0, "tempC": 15, "dewpointC": 4,
                  "wxString": "", "clouds": []}),
        ("KXXX", None),
    ]
    for sid, c in tests:
        icao, cat, body = format_lines(sid, c)
        print("%-5s [%-4s] : %s" % (icao, cat, "  |  ".join(body)))
