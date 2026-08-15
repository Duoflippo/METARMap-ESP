# display.py — optional screen that rotates through METAR conditions.
#
# Supports TWO displays, auto-detected (display_type = auto|oled|tft|off):
#   * OLED  — Adafruit 1.5" 128x128 grayscale SSD1327, I2C via STEMMA QT (#4741)
#   * TFT   — 320x240 color ILI9341 over SPI (e.g. SparkFun COM-28380)
#             wiring: SCK->SCK, MOSI->MOSI, CS->A0, DC->A1, RESET->A2,
#             backlight->3V, VCC->3V, GND->GND   (LEDs stay on A3)
#
# "auto" tries the I2C OLED first, then falls back to the SPI TFT. Auto-disables
# cleanly if neither the display nor its driver library is present.
#
# On the color TFT, the flight category is drawn in its LED color.
# The pure text helpers are unit-testable on desktop.

# Category colors (0xRRGGBB) matching the LED strip.
CAT_COLORS = {"VFR": 0x00FF00, "MVFR": 0x0000FF, "IFR": 0xFF0000, "LIFR": 0xFF00FF}
WHITE = 0xFFFFFF


def cat_color(cat):
    return CAT_COLORS.get((cat or "").upper(), WHITE)


# --- METAR present-weather decoding ---------------------------------------
_WX_INTENS = {"-": "Lt ", "+": "Hvy "}
_WX_DESC = {"MI": "Shallow", "PR": "Partial", "BC": "Patchy", "DR": "Drifting",
            "BL": "Blowing", "SH": "Showers", "TS": "T-storm", "FZ": "Freezing",
            "VC": "Nearby"}
_WX_PHEN = {"DZ": "Drizzle", "RA": "Rain", "SN": "Snow", "SG": "Snow Grains",
            "IC": "Ice Crystals", "PL": "Ice Pellets", "GR": "Hail", "GS": "Sm Hail",
            "UP": "Precip", "BR": "Mist", "FG": "Fog", "FU": "Smoke", "VA": "Ash",
            "DU": "Dust", "SA": "Sand", "HZ": "Haze", "PY": "Spray", "PO": "Whirls",
            "SQ": "Squall", "FC": "Funnel", "SS": "Sandstorm", "DS": "Duststorm"}


def decode_wx(wx):
    """'-RA BR' -> 'Lt Rain, Mist'.  'HZ' -> 'Haze'.  '' if none."""
    wx = (wx or "").strip().upper()
    if not wx:
        return ""
    parts = []
    for tok in wx.split():
        pre = ""
        if tok[:1] in ("-", "+"):
            pre = _WX_INTENS.get(tok[0], "")
            tok = tok[1:]
        words = []
        k = 0
        while k + 2 <= len(tok):
            code = tok[k:k + 2]
            words.append(_WX_DESC.get(code) or _WX_PHEN.get(code) or code)
            k += 2
        phrase = (pre + " ".join(words)).strip()
        if phrase:
            parts.append(phrase)
    return ", ".join(parts)


def _altimeter(raw):
    """'A3015' -> '30.15' (inHg); 'Q1013' -> '1013hPa'."""
    for tok in (raw or "").split():
        if len(tok) == 5 and tok[1:].isdigit():
            if tok[0] == "A":
                return "%s.%s" % (tok[1:3], tok[3:5])
            if tok[0] == "Q":
                return tok[1:] + "hPa"
    return ""


def _obs_time(raw):
    """Observation time token from the raw METAR, e.g. '121953Z' (DDHHMMZ, UTC)."""
    for tok in (raw or "").split():
        if len(tok) == 7 and tok.endswith("Z") and tok[:6].isdigit():
            return tok
    return ""


def _cloud_str(layer):
    """A METAR-style cloud layer like 'BKN014' (cover + base in hundreds of ft)."""
    cover = (layer.get("cover") or "").upper()
    base = layer.get("base")
    if base is not None and cover and cover not in ("CLR", "SKC", "NSC", "NCD"):
        return "%s%03d" % (cover, int(base) // 100)
    return cover or "SKC"


def _cloud_lines(clouds, budget=20):
    """Pack cloud layers onto shared lines, wrapping only when a line is full."""
    if not clouds:
        return ["CLR"]
    lines = []
    cur = ""
    for layer in clouds:
        code = _cloud_str(layer)
        cand = code if not cur else cur + " " + code
        if len(cand) > budget and cur:
            lines.append(cur)
            cur = code
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def format_lines(sid, c, cloud_budget=20):
    """Return (icao, category, [body lines]) for one station."""
    if c is None:
        return sid, "", ["No data"]

    cat = c.get("flightCategory") or "?"

    wdir = c.get("windDir")
    spd = c.get("windSpeed", 0)
    gust = c.get("windGustSpeed", 0)
    if spd == 0 and not gust:
        wind = "Calm"
    else:
        wind = "%s@%d" % ("VRB" if wdir is None else wdir, spd)
        if gust:
            wind += "G%d" % gust
        wind += "kt"
    body = ["Wind " + wind]

    wxs = decode_wx(c.get("wxString"))
    if wxs:
        body.append(wxs)

    vis = c.get("visibility")
    body.append(("Vis %gSM" % vis) if vis is not None else "Vis ?")

    # Only the two LOWEST cloud layers (includes the ceiling) so present weather
    # has room; on a full screen the obs-time line drops before the weather does.
    clouds = c.get("clouds") or []
    if clouds:
        clouds = sorted(clouds,
                        key=lambda lyr: lyr.get("base") if lyr.get("base") is not None else 99999)[:2]
    body.extend(_cloud_lines(clouds, cloud_budget))

    t = c.get("tempC")
    d = c.get("dewpointC")
    if t is not None or d is not None:
        body.append("Temp %s/%sC" % (t if t is not None else "?", d if d is not None else "?"))

    alt = _altimeter(c.get("raw"))
    if alt:
        body.append("Alt " + alt)

    tz = _obs_time(c.get("raw"))
    if tz:
        body.append("Obs " + tz)

    return sid, cat, body


# --- Screen backends -------------------------------------------------------

class _OledScreen:
    """128x128 grayscale SSD1327 over I2C. Category shown white (grayscale)."""
    name = "OLED"
    ADDRS = (0x3D, 0x3C)
    cloud_budget = 20      # scale-1 body, ~21 chars/line

    def __init__(self):
        import board
        import displayio
        import terminalio
        import adafruit_ssd1327
        from adafruit_display_text import label
        try:
            from i2cdisplaybus import I2CDisplayBus
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
            raise last_err or RuntimeError("no OLED on I2C")

        disp = adafruit_ssd1327.SSD1327(bus, width=128, height=128)
        self.group = displayio.Group()
        _root(disp, self.group)

        self._icao = label.Label(terminalio.FONT, text="", scale=2,
                                 anchor_point=(0.0, 0.0), anchored_position=(2, 2))
        self._cat = label.Label(terminalio.FONT, text="", scale=2,
                                anchor_point=(1.0, 0.0), anchored_position=(126, 2))
        self.group.append(self._icao)
        self.group.append(self._cat)
        self._body = [label.Label(terminalio.FONT, text="",
                                  anchor_point=(0.0, 0.0), anchored_position=(2, 24 + i * 12))
                      for i in range(8)]
        for lbl in self._body:
            self.group.append(lbl)

    def update(self, icao, cat, color, body):
        self._icao.text = icao
        self._cat.text = cat                 # grayscale: keep white
        for i, lbl in enumerate(self._body):
            lbl.text = body[i] if i < len(body) else ""


class _TftScreen:
    """320x240 color ILI9341 over SPI. Category drawn in its LED color."""
    name = "TFT"
    cloud_budget = 15      # scale-3 body, ~17 chars/line -> wrap sooner

    def __init__(self, rotation=0):
        import board
        import displayio
        import terminalio
        import adafruit_ili9341
        from adafruit_display_text import label
        try:
            from fourwire import FourWire
        except ImportError:
            from displayio import FourWire

        displayio.release_displays()
        spi = board.SPI()
        bus = FourWire(spi, command=board.A1, chip_select=board.A0, reset=board.A2)
        disp = adafruit_ili9341.ILI9341(bus, width=320, height=240, rotation=rotation)
        self.group = displayio.Group()
        _root(disp, self.group)

        # Big header: airport code (left) + category (right, in its LED color).
        self._icao = label.Label(terminalio.FONT, text="", scale=5, color=WHITE,
                                 anchor_point=(0.0, 0.0), anchored_position=(6, 4))
        self._cat = label.Label(terminalio.FONT, text="", scale=5, color=WHITE,
                                anchor_point=(1.0, 0.0), anchored_position=(316, 4))
        self.group.append(self._icao)
        self.group.append(self._cat)
        # Body at scale 3 -> ~6 lines fit below the taller header.
        self._body = [label.Label(terminalio.FONT, text="", scale=3, color=WHITE,
                                  anchor_point=(0.0, 0.0), anchored_position=(6, 66 + i * 29))
                      for i in range(6)]
        for lbl in self._body:
            self.group.append(lbl)

    def update(self, icao, cat, color, body):
        self._icao.text = icao
        self._cat.text = cat
        self._cat.color = color              # category in its LED color
        for i, lbl in enumerate(self._body):
            lbl.text = body[i] if i < len(body) else ""


def _root(disp, group):
    try:
        disp.root_group = group
    except AttributeError:
        disp.show(group)                     # older displayio


def _make_screen(kind, rotation):
    kind = (kind or "auto").lower()
    if kind == "off":
        return None
    if kind in ("auto", "oled"):
        try:
            return _OledScreen()
        except Exception as e:
            if kind == "oled":
                raise
            print("display: no OLED (%s); trying TFT" % e)
    if kind in ("auto", "tft"):
        return _TftScreen(rotation)
    return None


class MetarDisplay:
    def __init__(self, rotation_secs=5.0, kind="auto", rotation=0):
        self.rotation_secs = rotation_secs
        self.ok = False
        self._airports = []
        self._idx = 0
        self._last = 0.0
        self._screen = None
        try:
            self._screen = _make_screen(kind, rotation)
            self.ok = self._screen is not None
            if self.ok:
                print("display: %s ready" % self._screen.name)
            else:
                print("display: none configured")
        except Exception as e:
            print("display: init skipped:", e)

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
        budget = getattr(self._screen, "cloud_budget", 20)
        icao, cat, body = format_lines(sid, (conditions or {}).get(sid), budget)
        self._screen.update(icao, cat, cat_color(cat), body)


# --- desktop self-test: `python display.py` ---------------------------------
if __name__ == "__main__":
    tests = [
        ("KSEA", {"flightCategory": "VFR", "windDir": 270, "windSpeed": 6,
                  "windGustSpeed": 0, "visibility": 10.0, "tempC": 19, "dewpointC": 7,
                  "wxString": "", "clouds": [{"cover": "FEW", "base": 3000}],
                  "raw": "METAR KSEA 121953Z 27006KT 10SM FEW030 19/07 A3015"}),
        ("KJFK", {"flightCategory": "IFR", "windDir": None, "windSpeed": 12,
                  "windGustSpeed": 20, "visibility": 2.0, "tempC": 3, "dewpointC": 2,
                  "wxString": "-RA BR HZ", "clouds": [{"cover": "SCT", "base": 1400},
                  {"cover": "BKN", "base": 2500}, {"cover": "OVC", "base": 4000}],
                  "raw": "METAR KJFK 121951Z VRB12G20KT 2SM -RA BR SCT014 BKN025 OVC040 03/02 A2987"}),
    ]
    for sid, c in tests:
        icao, cat, body = format_lines(sid, c)
        print("%-5s [%-4s] color=0x%06X" % (icao, cat, cat_color(cat)))
        for line in body:
            print("      " + line)
