# metar_source.py
#
# The ONLY module that talks to aviationweather.gov. Everything the rest of the
# project needs comes out of here as a clean internal dict, so when the API
# changes again (it will), this is the single file that gets patched and pushed
# over-the-air. Keep all provider-specific knowledge in this file.
#
# Targets the CURRENT endpoint (the old dataserver.php / XML feed is retired):
#     https://aviationweather.gov/api/data/metar?ids=KSEA,KJFK&format=json
#
# CircuitPython-safe: no datetime, no xml, no typing. Uses epoch-second math so
# it runs unchanged on the QT Py (time.time() once the RTC is NTP-synced) and on
# desktop Python for testing.

API_BASE = "https://aviationweather.gov/api/data/metar"

# aviationweather.gov asks for a descriptive User-Agent (not a spoofed browser)
# and limits clients to 100 requests/min. One request per refresh is plenty.
DEFAULT_USER_AGENT = "METARMap/2.0 (github.com/YOURNAME/METARMap-ESP)"

# A station is considered "stale" if its newest observation is older than this.
DEFAULT_STALE_AFTER_MIN = 90


# ---------------------------------------------------------------------------
# The internal contract
# ---------------------------------------------------------------------------
# parse_metars() returns { "KSEA": station, ... } where each station is:
#
#   {
#     "stationId":     "KSEA",
#     "flightCategory":"VFR" | "MVFR" | "IFR" | "LIFR" | "",
#     "windDir":       int degrees, or None if variable/missing,
#     "windSpeed":     int knots,
#     "windGustSpeed": int knots (0 if not gusting),
#     "tempC":         float or None,
#     "dewpointC":     float or None,
#     "visibility":    float statute miles, or None,
#     "wxString":      "" | raw present-weather string,
#     "clouds":        [ {"cover": "BKN", "base": 3000}, ... ],
#     "raw":           full raw METAR text,
#     "obsEpoch":      int unix seconds of the observation,
#     "ageMin":        int minutes old (None if now_epoch not supplied),
#     "stale":         bool (False if now_epoch not supplied),
#     "wx": {                       # decoded hazard flags, policy-free
#       "thunderstorm":   bool,     # TS / lightning in the vicinity
#       "freezing":       bool,     # FZRA / FZDZ / PL ice pellets / FZFG
#       "snow":           bool,     # SN / SG
#       "fog":            bool,     # FG / BR mist
#       "heavyRain":      bool,     # +RA
#       "severe":         bool,     # tornado/funnel, squall, hail, dust/sandstorm
#       "icingPotential": bool,     # derived: near/below freezing + humid
#     },
#   }
# ---------------------------------------------------------------------------


def build_url(ids):
    """ids: list of ICAO identifiers (NULL/placeholder entries should be filtered out by the caller)."""
    return API_BASE + "?ids=" + ",".join(ids) + "&format=json"


def fetch_metars(session, ids, user_agent=DEFAULT_USER_AGENT):
    """Fetch raw JSON list from the API.

    `session` is anything with a .get(url, headers=...) -> response(.json(), .close()):
      - on the QT Py: an adafruit_requests.Session
      - on desktop:   the `requests` module itself
    """
    url = build_url(ids)
    resp = session.get(url, headers={"User-Agent": user_agent})
    try:
        return resp.json()
    finally:
        # adafruit_requests must release the socket; requests.close() is harmless.
        try:
            resp.close()
        except Exception:
            pass


def get_conditions(session, ids, now_epoch=None, stale_after_min=DEFAULT_STALE_AFTER_MIN,
                   user_agent=DEFAULT_USER_AGENT):
    """Convenience: fetch + parse in one call."""
    raw = fetch_metars(session, ids, user_agent=user_agent)
    return parse_metars(raw, now_epoch=now_epoch, stale_after_min=stale_after_min)


def parse_metars(raw_json, now_epoch=None, stale_after_min=DEFAULT_STALE_AFTER_MIN):
    """Turn the API's JSON list into the internal per-station dict. Pure: no I/O."""
    result = {}
    if not raw_json:
        return result
    for m in raw_json:
        station_id = m.get("icaoId")
        if not station_id:
            continue

        obs_epoch = _as_int(m.get("obsTime"), None)
        age_min = None
        stale = False
        if now_epoch is not None and obs_epoch is not None:
            age_min = int((now_epoch - obs_epoch) // 60)
            stale = age_min > stale_after_min

        temp_c = _as_float(m.get("temp"), None)
        dewp_c = _as_float(m.get("dewp"), None)
        raw_text = m.get("rawOb") or ""

        result[station_id] = {
            "stationId": station_id,
            "flightCategory": m.get("fltCat") or "",
            "windDir": _parse_wdir(m.get("wdir")),
            "windSpeed": _as_int(m.get("wspd"), 0),
            "windGustSpeed": _as_int(m.get("wgst"), 0),
            "tempC": temp_c,
            "dewpointC": dewp_c,
            "visibility": _parse_visib(m.get("visib")),
            "wxString": m.get("wxString") or "",
            "clouds": _parse_clouds(m.get("clouds")),
            "raw": raw_text,
            "obsEpoch": obs_epoch,
            "ageMin": age_min,
            "stale": stale,
            "wx": _parse_wx(m.get("wxString"), raw_text, temp_c, dewp_c),
        }
    return result


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------

def _as_int(v, default):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def _as_float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_wdir(v):
    """wind direction is an int, or the string 'VRB' for variable, or missing."""
    if v is None:
        return None
    return _as_int(v, None)


def _parse_visib(v):
    """visib arrives as a string: '10+', '6', '1 1/2', '1/2'. Return miles as float."""
    if v is None:
        return None
    s = str(v).replace("+", "").strip()
    if not s:
        return None
    try:
        if " " in s:                      # "1 1/2"
            whole, frac = s.split(" ", 1)
            num, den = frac.split("/")
            return float(whole) + float(num) / float(den)
        if "/" in s:                      # "1/2"
            num, den = s.split("/")
            return float(num) / float(den)
        return float(s)
    except (ValueError, ZeroDivisionError):
        return None


def _parse_clouds(clouds):
    out = []
    if not clouds:
        return out
    for c in clouds:
        out.append({"cover": c.get("cover") or "", "base": _as_int(c.get("base"), None)})
    return out


def _parse_wx(wx_string, raw_text, temp_c, dewp_c):
    """Decode present-weather codes into policy-free hazard flags.

    Flags may overlap (e.g. FZFG is both freezing and fog); the render layer's
    precedence engine decides which one actually drives the LED.
    """
    wx = (wx_string or "").upper()
    raw = (raw_text or "").upper()
    tokens = wx.split()

    def has(code):
        return any(code in t for t in tokens)

    # Thunderstorm: TS in the present weather, or LTG noted in the raw remarks
    # (but not "TSNO" = thunderstorm-sensor-not-available).
    thunderstorm = has("TS") or ("LTG" in raw and "TSNO" not in raw)

    # Freezing precipitation is the top routine hazard: FZRA/FZDZ/FZFG + ice pellets.
    freezing = has("FZ") or has("PL")

    snow = has("SN") or has("SG")
    fog = has("FG") or has("BR")
    heavy_rain = any(t.startswith("+") and "RA" in t for t in tokens)

    # Severe / convective: tornado or funnel (FC), squall (SQ), hail (GR),
    # dust/sandstorm (DS/SS).
    severe = has("FC") or has("SQ") or has("GR") or has("DS") or has("SS")

    return {
        "thunderstorm": thunderstorm,
        "freezing": freezing,
        "snow": snow,
        "fog": fog,
        "heavyRain": heavy_rain,
        "severe": severe,
        "icingPotential": _icing_potential(temp_c, dewp_c),
    }


def _icing_potential(temp_c, dewp_c):
    """Derived, not in wxString: near/below freezing with a small temp-dewpoint
    spread (humid) => frost / structural-icing potential on the ground."""
    if temp_c is None or dewp_c is None:
        return False
    return temp_c <= 3.0 and (temp_c - dewp_c) <= 3.0


# ---------------------------------------------------------------------------
# Desktop self-test: `python metar_source.py KSEA KJFK KORD`
# (Does not run on the device, where this file is imported as a module.)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import time

    ids = sys.argv[1:] or ["KSEA", "KJFK", "KORD", "KMCI", "KDEN"]

    # Prefer `requests` if installed, else fall back to urllib (stdlib).
    try:
        import requests as session
    except ImportError:
        import json
        import urllib.request

        class _UrllibSession:
            def get(self, url, headers=None):
                req = urllib.request.Request(url, headers=headers or {})
                self._body = urllib.request.urlopen(req).read()
                return self

            def json(self):
                return json.loads(self._body)

            def close(self):
                pass

        session = _UrllibSession()

    now = int(time.time())
    conditions = get_conditions(session, ids, now_epoch=now)

    for sid in ids:
        c = conditions.get(sid)
        if c is None:
            print("%-5s  (no data returned)" % sid)
            continue
        active = [k for k, v in c["wx"].items() if v]
        print("%-5s  %-4s  wind %s@%dG%d  vis %s  age %smin%s  wx:%s" % (
            sid,
            c["flightCategory"] or "?",
            c["windDir"] if c["windDir"] is not None else "VRB",
            c["windSpeed"], c["windGustSpeed"],
            c["visibility"], c["ageMin"],
            "  [STALE]" if c["stale"] else "",
            ",".join(active) if active else "none",
        ))
