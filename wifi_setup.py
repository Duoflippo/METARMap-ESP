# wifi_setup.py — get the map onto ANY user's WiFi, with no computer needed.
#
# Flow (ensure_connected):
#   1. Try to connect using credentials stored in config.json.
#   2. If there are none, or they fail (e.g. the user changed routers), start a
#      "METARMap-Setup" access point and serve a setup page.
#   3. The user joins that network on a phone, opens http://192.168.4.1, picks
#      their WiFi from a scanned list, enters the password -> saved to
#      config.json -> the device reboots and connects for real.
#
# All hardware/library imports are inside functions so this file imports cleanly
# on desktop for syntax checks.

import time

AP_SSID = "METARMap-Setup"
AP_PASSWORD = ""          # "" = open network, so a phone can join with no password
AP_IP = "192.168.4.1"

_state = {"reset_at": None}   # set when the user submits, triggers a reboot


def ensure_connected(config, pixels=None, config_path="config.json"):
    """Return True once connected. If it can't connect, it enters the setup
    portal, which blocks until the user configures WiFi and the device reboots."""
    ssid = config.get("wifiSsid")
    password = config.get("wifiPassword", "")

    if ssid and connect(ssid, password):
        return True

    print("wifi_setup: no working credentials, entering setup mode")
    start_provisioning(config, pixels=pixels, config_path=config_path)
    return False   # unreachable (portal reboots the device)


def connect(ssid, password="", retries=3):
    import wifi
    for attempt in range(1, retries + 1):
        try:
            print("wifi_setup: connecting to '%s' (try %d/%d)" % (ssid, attempt, retries))
            wifi.radio.connect(ssid, password)
            print("wifi_setup: connected, IP =", wifi.radio.ipv4_address)
            return True
        except (ConnectionError, RuntimeError) as e:
            print("wifi_setup: attempt %d failed: %s" % (attempt, e))
            time.sleep(2)
    return False


def start_provisioning(config, pixels=None, config_path="config.json"):
    import wifi
    import socketpool
    import microcontroller
    from adafruit_httpserver import Server, Response, POST

    # A visible "I'm in setup mode" cue on the strip (dim blue) if we have pixels.
    if pixels is not None:
        try:
            pixels.brightness = 0.3
            pixels.fill((0, 0, 60))
            pixels.show()
        except Exception:
            pass

    networks = _scan(wifi)
    print("wifi_setup: starting AP '%s' (open)" % AP_SSID)
    wifi.radio.start_ap(AP_SSID, AP_PASSWORD)

    pool = socketpool.SocketPool(wifi.radio)
    server = Server(pool, debug=False)

    def _portal(request):
        return Response(request, _form_html(networks), content_type="text/html")

    # Serve the setup page for the root AND for the OS connectivity-probe URLs
    # (Apple / Android / Windows). Returning the page instead of the expected
    # probe response is what makes the phone flag a captive portal and auto-open
    # the sign-in sheet. With wildcard DNS below, these hostnames all resolve here.
    for path in ("/", "/hotspot-detect.html", "/library/test/success.html",
                 "/generate_204", "/gen_204", "/connecttest.txt", "/ncsi.txt",
                 "/redirect", "/success.txt", "/canonical.html"):
        server.route(path, "GET")(_portal)

    @server.route("/save", POST)
    def _save(request):
        form = _parse_form(request.body)
        ssid = form.get("ssid_manual") or form.get("ssid") or ""
        password = form.get("password", "")
        config["wifiSsid"] = ssid
        config["wifiPassword"] = password
        _save_config(config, config_path)
        _state["reset_at"] = time.monotonic() + 2.5   # let the response flush first
        return Response(request, _saved_html(ssid), content_type="text/html")

    server.start(AP_IP)

    # Wildcard DNS -> makes any hostname resolve to us, triggering the popup.
    # Best-effort: if it can't start, the manual-IP flow still works.
    dns = None
    try:
        import captive_dns
        dns = captive_dns.CaptiveDNS(pool, AP_IP)
        print("wifi_setup: captive DNS active")
    except Exception as e:
        print("wifi_setup: captive DNS unavailable (manual IP still works):", e)

    print("wifi_setup: open http://%s to configure" % AP_IP)
    while True:
        try:
            server.poll()
            if dns is not None:
                dns.poll()
        except Exception as e:
            print("wifi_setup: server error:", e)
        if _state["reset_at"] and time.monotonic() > _state["reset_at"]:
            print("wifi_setup: credentials saved, rebooting")
            microcontroller.reset()
        time.sleep(0.02)


# --- helpers -----------------------------------------------------------------

def _scan(wifi):
    """Return a de-duplicated list of nearby SSIDs (strongest first-ish)."""
    seen = []
    try:
        for net in wifi.radio.start_scanning_networks():
            ssid = net.ssid
            if ssid and ssid not in seen:
                seen.append(ssid)
    except Exception as e:
        print("wifi_setup: scan error:", e)
    finally:
        try:
            wifi.radio.stop_scanning_networks()
        except Exception:
            pass
    return seen


def _save_config(config, path):
    import json
    try:
        with open(path, "w") as f:
            json.dump(config, f)
        print("wifi_setup: saved WiFi to", path)
    except OSError as e:
        # Fails if flash isn't writable (e.g. USB host owns it). boot.py remounts
        # it writable, so this should only happen while tethered to a computer.
        print("wifi_setup: could NOT save config:", e)


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
    """Parse an application/x-www-form-urlencoded POST body into a dict."""
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


def _form_html(networks):
    options = "".join("<option value='%s'>%s</option>" % (n, n) for n in networks)
    return (
        "<!DOCTYPE html><html><head><meta name='viewport' "
        "content='width=device-width,initial-scale=1'><title>METARMap Setup</title>"
        "<style>body{font-family:sans-serif;max-width:420px;margin:2em auto;padding:0 1em}"
        "h1{font-size:1.3em}label{display:block;margin:1em 0 .3em}"
        "select,input{width:100%;padding:.6em;font-size:1em;box-sizing:border-box}"
        "button{margin-top:1.5em;width:100%;padding:.8em;font-size:1em}</style></head><body>"
        "<h1>METARMap WiFi Setup</h1>"
        "<form action='/save' method='post'>"
        "<label>Network</label><select name='ssid'>" + options + "</select>"
        "<label>Or type a hidden/other network</label>"
        "<input name='ssid_manual' placeholder='(optional)'>"
        "<label>Password</label><input name='password' type='password'>"
        "<button type='submit'>Save &amp; Connect</button>"
        "</form></body></html>"
    )


def _saved_html(ssid):
    return (
        "<!DOCTYPE html><html><head><meta name='viewport' "
        "content='width=device-width,initial-scale=1'><title>Saved</title>"
        "<style>body{font-family:sans-serif;max-width:420px;margin:2em auto;padding:0 1em}</style>"
        "</head><body><h1>Saved</h1><p>Connecting to <b>" + ssid + "</b> and restarting."
        " If it doesn't join, the <b>METARMap-Setup</b> network will reappear so you"
        " can try again.</p></body></html>"
    )
