# render.py — turns parsed conditions into SK6812 (RGB) colors + animations.
#
# The heart of the map. A station can be several things at once (IFR + freezing
# + high wind), but one LED can only show one animation, so this runs a strict
# PRECEDENCE ENGINE: the most important hazard wins. Every effect is individually
# toggleable via config, so any climate gets the full menu and the user prunes.
#
# Precedence (highest first):
#   no-data/off > stale > severe > freezing > thunderstorm > high-wind
#   > snow > low-wind > fog > heavy-rain > icing > plain flight category
#
# Desktop-testable: run `python render.py` to print the precedence table with a
# mock strip (no hardware needed). On the device, code.py passes a real NeoPixel.

import time
import random

# --- Colors (plain RGB; NeoPixel pixel_order=GRB handles SK6812 wiring) -------
CLEAR   = (0, 0, 0)
GREEN   = (0, 255, 0)      # VFR
BLUE    = (0, 0, 255)      # MVFR
RED     = (255, 0, 0)      # IFR
MAGENTA = (255, 0, 255)    # LIFR
WHITE   = (255, 255, 255)  # lightning
YELLOW  = (255, 255, 0)    # high winds
ROSE    = (255, 80, 140)   # freezing precip (distinct from magenta LIFR)
CYAN    = (0, 255, 255)    # icing tint
SNOW    = (170, 170, 210)  # cool-white snow sparkle

CATEGORY_COLORS = {"VFR": GREEN, "MVFR": BLUE, "IFR": RED, "LIFR": MAGENTA}

# --- Default settings (config.json overrides any of these) -------------------
DEFAULTS = {
    # master toggles for each weather behavior
    "wind_enabled":      True,
    "lightning_enabled": True,
    "freezing_enabled":  True,
    "snow_enabled":      True,
    "severe_enabled":    True,
    "fog_enabled":       False,
    "rain_enabled":      False,
    "icing_enabled":     False,
    "stale_enabled":     True,

    # thresholds (knots)
    "wind_threshold":       15,   # blink/fade at or above this
    "high_wind_threshold":  25,   # yellow flash at or above this (-1 disables)
    "always_blink_gusts":   False,

    # wind style
    "fade_instead_of_blink": True,

    # animation periods / tuning (seconds unless noted)
    "blink_speed":       1.0,   # half-cycle for wind blink/fade
    "lightning_period":  0.8,
    "freezing_period":   1.4,
    "severe_period":     0.16,  # fast strobe
    "fog_period":        4.0,   # slow breathe
    "rain_period":       0.7,
    "snow_density":      0.20,  # chance a snow pixel sparkles white each frame
}


# --- Small color helpers -----------------------------------------------------

def _scale(c, f):
    return (int(c[0] * f), int(c[1] * f), int(c[2] * f))


def _blend(c1, c2, f):
    """f=0 -> c1, f=1 -> c2."""
    return (
        int(c1[0] + (c2[0] - c1[0]) * f),
        int(c1[1] + (c2[1] - c1[1]) * f),
        int(c1[2] + (c2[2] - c1[2]) * f),
    )


def _saw(t, period):
    """0..1 sawtooth."""
    return (t % period) / period


def _tri(t, period):
    """0..1..0 triangle wave."""
    x = (t % period) / period
    return 2.0 * x if x < 0.5 else 2.0 * (1.0 - x)


class Renderer:
    def __init__(self, pixels, airports, config=None):
        # `airports` is the LED order; "NULL" entries are LEDs with no airport.
        self.pixels = pixels
        self.airports = airports
        self.cfg = dict(DEFAULTS)
        if config:
            self.cfg.update(config)

    def render_frame(self, conditions):
        """Compute every pixel for the current instant and push to the strip.
        Call this ~10-15x/second so fades and twinkles look smooth."""
        t = time.monotonic()
        for i, code in enumerate(self.airports):
            if code == "NULL":
                self.pixels[i] = CLEAR
                continue
            self.pixels[i] = self.pixel_color(conditions.get(code), t)
        self.pixels.show()

    def pixel_color(self, cond, t):
        """The precedence engine: pick the single winning effect for one station."""
        if cond is None:
            return CLEAR
        base = CATEGORY_COLORS.get(cond.get("flightCategory"))
        if base is None:
            return CLEAR

        cfg = self.cfg
        wx = cond.get("wx", {})
        wind = cond.get("windSpeed", 0)
        gust = cond.get("windGustSpeed", 0)

        # 1. Stale data: the report (and its hazards) may be hours old. Show the
        #    category clearly subdued with a slow pulse rather than trusting flags.
        if cfg["stale_enabled"] and cond.get("stale"):
            f = _tri(t, 5.0)
            return _blend(_scale(base, 0.08), _scale(base, 0.35), f)

        # 2. Severe: tornado/funnel, squall, hail, dust/sandstorm -> fast strobe.
        if cfg["severe_enabled"] and wx.get("severe"):
            return base if _saw(t, cfg["severe_period"]) < 0.5 else WHITE

        # 3. Freezing precip -> rose flash over category.
        if cfg["freezing_enabled"] and wx.get("freezing"):
            return base if _saw(t, cfg["freezing_period"]) < 0.5 else ROSE

        # 4. Thunderstorm / lightning -> white flash.
        if cfg["lightning_enabled"] and wx.get("thunderstorm"):
            return base if _saw(t, cfg["lightning_period"]) < 0.5 else WHITE

        # 5. High wind -> yellow flash.
        hwt = cfg["high_wind_threshold"]
        if cfg["wind_enabled"] and hwt >= 0 and (wind >= hwt or gust >= hwt):
            return base if _saw(t, cfg["blink_speed"] * 2) < 0.5 else YELLOW

        # 6. Snow -> cool-white twinkle over category.
        if cfg["snow_enabled"] and wx.get("snow"):
            return SNOW if random.random() < cfg["snow_density"] else base

        # 7. Low-tier wind -> fade or blink the category color.
        wt = cfg["wind_threshold"]
        gusting = gust > 0
        windy = wind >= wt or gust >= wt or (cfg["always_blink_gusts"] and gusting)
        if cfg["wind_enabled"] and windy:
            if cfg["fade_instead_of_blink"]:
                return _blend(_scale(base, 0.25), base, _tri(t, cfg["blink_speed"] * 2))
            return base if _saw(t, cfg["blink_speed"] * 2) < 0.5 else CLEAR

        # 8. Fog / obscuration -> slow breathe.
        if cfg["fog_enabled"] and wx.get("fog"):
            return _blend(_scale(base, 0.25), base, _tri(t, cfg["fog_period"]))

        # 9. Heavy rain -> gentle blue shimmer.
        if cfg["rain_enabled"] and wx.get("heavyRain"):
            return _blend(base, BLUE, 0.15 + 0.15 * _tri(t, cfg["rain_period"]))

        # 10. Icing potential -> steady cyan tint.
        if cfg["icing_enabled"] and wx.get("icingPotential"):
            return _blend(base, CYAN, 0.3)

        # 11. Nothing special -> plain flight category.
        return base


# --- Desktop smoke test: `python render.py` ---------------------------------
if __name__ == "__main__":
    class _MockPixels(list):
        def show(self):
            pass

    scenarios = [
        ("plain VFR",     {"flightCategory": "VFR",  "wx": {}}),
        ("IFR + TS",      {"flightCategory": "IFR",  "wx": {"thunderstorm": True}}),
        ("LIFR + freeze", {"flightCategory": "LIFR", "wx": {"freezing": True}}),
        ("MVFR + snow",   {"flightCategory": "MVFR", "wx": {"snow": True}}),
        ("VFR high wind", {"flightCategory": "VFR",  "wx": {}, "windSpeed": 30}),
        ("VFR low wind",  {"flightCategory": "VFR",  "wx": {}, "windSpeed": 18}),
        ("IFR + severe",  {"flightCategory": "IFR",  "wx": {"severe": True}}),
        ("stale VFR",     {"flightCategory": "VFR",  "wx": {}, "stale": True}),
        # freezing outranks high wind when both present:
        ("LIFR freeze+wind", {"flightCategory": "LIFR", "wx": {"freezing": True}, "windSpeed": 40}),
    ]
    codes = [s[0] for s in scenarios]
    conds = {name: c for name, c in scenarios}

    r = Renderer(_MockPixels([CLEAR] * len(scenarios)), codes)
    print("Distinct colors each scenario cycles through (sampled over 5s):\n")
    for name in codes:
        seen = []
        tt = 0.0
        while tt < 5.0:
            c = r.pixel_color(conds[name], tt)
            if c not in seen:
                seen.append(c)
            tt += 0.05
        print("  %-18s -> %s" % (name, seen))
