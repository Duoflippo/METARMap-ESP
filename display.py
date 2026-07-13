# display.py — optional I2C OLED that rotates through METAR conditions.
#
# Target: Adafruit 1.5" 128x128 grayscale OLED, SSD1327 driver, STEMMA QT (#4741).
# Plugs into the QT Py's STEMMA QT port (I2C) with no soldering, independent of
# the LED data pin (A0). Auto-disables cleanly if the display OR its libraries
# are absent, so shipping this module to a board without them is harmless.
#
# format_lines() is pure (no hardware) so it can be unit-tested on desktop.


def format_lines(sid, c):
    """Return (title, [body lines]) of text to show for one station."""
    if c is None:
        return sid, ["(no data)"]
    wdir = c.get("windDir")
    wind = "%s@%d" % ("VRB" if wdir is None else wdir, c.get("windSpeed", 0))
    gust = c.get("windGustSpeed", 0)
    if gust:
        wind += "G%d" % gust
    lines = [c.get("flightCategory") or "?", "Wind " + wind + "kt"]
    vis = c.get("visibility")
    lines.append("Vis " + ("%g" % vis if vis is not None else "?") + "sm")
    t = c.get("tempC")
    d = c.get("dewpointC")
    lines.append("T/Dp %s/%s C" % (t if t is not None else "?", d if d is not None else "?"))
    wx = [k for k, v in (c.get("wx") or {}).items() if v]
    if wx:
        lines.append(",".join(wx))
    age = c.get("ageMin")
    if c.get("stale"):
        lines.append("STALE %s min" % age)
    elif age is not None:
        lines.append("obs %s min" % age)
    return sid, lines


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
        import adafruit_displayio_ssd1327
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

        self.display = adafruit_displayio_ssd1327.SSD1327(bus, width=128, height=128)
        self.group = displayio.Group()
        try:
            self.display.root_group = self.group
        except AttributeError:
            self.display.show(self.group)                 # older displayio

        self._title = label.Label(terminalio.FONT, text="", scale=2, x=4, y=14)
        self._body = [label.Label(terminalio.FONT, text="", x=4, y=44 + i * 14)
                      for i in range(5)]
        self.group.append(self._title)
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
        title, lines = format_lines(sid, (conditions or {}).get(sid))
        self._title.text = title
        for i, lbl in enumerate(self._body):
            lbl.text = lines[i] if i < len(lines) else ""


# --- desktop self-test: `python display.py` ---------------------------------
if __name__ == "__main__":
    tests = [
        ("KSEA", {"flightCategory": "VFR", "windDir": 270, "windSpeed": 6,
                  "windGustSpeed": 0, "visibility": 10.0, "tempC": 19, "dewpointC": 7,
                  "wx": {}, "ageMin": 12, "stale": False}),
        ("KJFK", {"flightCategory": "IFR", "windDir": None, "windSpeed": 12,
                  "windGustSpeed": 20, "visibility": 2.0, "tempC": 3, "dewpointC": 2,
                  "wx": {"freezing": True, "snow": True}, "ageMin": 150, "stale": True}),
        ("KXXX", None),
    ]
    for sid, c in tests:
        title, lines = format_lines(sid, c)
        print("%-5s -> %s | %s" % (title, lines[0], " / ".join(lines[1:])))
