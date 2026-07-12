# updater.py — over-the-air self-update from the public GitHub repo.
#
# Pulls a version manifest; if it's newer than what's installed, downloads the
# listed files, verifies each one, backs up the current copies, installs, and
# reboots. boot.py is the safety net: if the new code won't run, it rolls back.
#
# Because the repo is public, every fetch is anonymous — no token on the device.

import microcontroller
import os

# Where devices look for updates. Point this at your public repo.
REPO = "Duoflippo/METARMap-ESP"
BRANCH = "main"
RAW_BASE = "https://raw.githubusercontent.com/%s/%s/" % (REPO, BRANCH)
MANIFEST_URL = RAW_BASE + "version.json"

LOCAL_VERSION_FILE = "version.json"

# NVM byte indices, shared with boot.py's rollback watchdog.
_I_PENDING = 1
_I_CRASHES = 2


def _read_local_version():
    try:
        import json
        with open(LOCAL_VERSION_FILE) as f:
            return json.load(f).get("version", "0.0.0")
    except (OSError, ValueError):
        return "0.0.0"


def _vtuple(v):
    out = []
    for part in str(v).split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def _sha256(data):
    """Hex sha256 if a hash lib is available, else None (we then fall back to a
    size-only check). MUST NOT raise — a hashing hiccup must never brick OTA."""
    try:
        import hashlib
    except ImportError:
        try:
            import adafruit_hashlib as hashlib
        except ImportError:
            return None
    h = None
    try:
        h = hashlib.sha256()
    except AttributeError:
        try:
            h = hashlib.new("sha256")   # adafruit_hashlib has no sha256() shortcut
        except Exception:
            return None
    try:
        h.update(data)
        return h.hexdigest()
    except Exception:
        return None


def check_and_update(session, user_agent="METARMap-Updater/2.0"):
    """Check the manifest and install if newer. Returns False if nothing to do;
    on a successful update the device resets and this never returns."""
    headers = {"User-Agent": user_agent}
    try:
        resp = session.get(MANIFEST_URL, headers=headers)
        manifest = resp.json()
        resp.close()
    except Exception as e:
        print("updater: could not fetch manifest: %s" % e)
        return False

    remote_v = manifest.get("version", "0.0.0")
    local_v = _read_local_version()
    if _vtuple(remote_v) <= _vtuple(local_v):
        print("updater: up to date (v%s)" % local_v)
        return False

    print("updater: v%s -> v%s, downloading..." % (local_v, remote_v))

    # 1. Download + verify EVERYTHING into memory first. Install nothing until
    #    all files pass, so a dropped connection leaves the device untouched.
    staged = []  # list of (target_path, bytes)
    for entry in manifest.get("files", []):
        path = entry.get("path")
        try:
            r = session.get(RAW_BASE + path, headers=headers)
            data = r.content
            r.close()
        except Exception as e:
            print("updater: download failed for %s: %s" % (path, e))
            return False

        want_size = entry.get("size")
        if want_size is not None and len(data) != want_size:
            print("updater: size mismatch for %s (%d != %d)" % (path, len(data), want_size))
            return False
        want_sha = entry.get("sha256")
        if want_sha:
            got = _sha256(data)
            if got is not None and got != want_sha:
                print("updater: sha256 mismatch for %s" % path)
                return False
        staged.append((path, data))

    # Stage the manifest too, so the on-device version matches the installed code.
    try:
        r = session.get(MANIFEST_URL, headers=headers)
        staged.append((LOCAL_VERSION_FILE, r.content))
        r.close()
    except Exception:
        pass

    # 2. Write all .new files first (still non-destructive to running code).
    for path, data in staged:
        try:
            with open(path + ".new", "wb") as f:
                f.write(data)
        except OSError as e:
            print("updater: write failed for %s: %s (aborting)" % (path, e))
            return False

    # 3. Install: back up current -> .bak, then swap .new into place.
    for path, _ in staged:
        try:
            try:
                os.remove(path + ".bak")
            except OSError:
                pass
            try:
                os.rename(path, path + ".bak")  # keep the running version as backup
            except OSError:
                pass  # brand-new module, nothing to back up
            os.rename(path + ".new", path)
        except OSError as e:
            print("updater: install failed for %s: %s" % (path, e))

    # 4. Arm the rollback watchdog and reboot into the new code.
    microcontroller.nvm[_I_PENDING] = 1
    microcontroller.nvm[_I_CRASHES] = 0
    print("updater: installed v%s, rebooting" % remote_v)
    microcontroller.reset()
    return True  # unreachable


def confirm_healthy():
    """code.py calls this once the map is running normally. It accepts a pending
    update by disarming the rollback watchdog."""
    try:
        microcontroller.nvm[_I_PENDING] = 0
        microcontroller.nvm[_I_CRASHES] = 0
    except Exception:
        pass
