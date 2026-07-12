#!/usr/bin/env python3
# tools/make_manifest.py — regenerate version.json for OTA releases.
#
# Run on your desktop after changing any device code, then commit + push:
#     python tools/make_manifest.py 2.0.1
#
# It hashes each managed device file so the updater can verify downloads.
# NOTE: only list actual device *code* here — never config.json or secrets,
# so OTA can never overwrite a user's settings or WiFi password.

import hashlib
import json
import os
import sys

DEVICE_FILES = [
    "boot.py",
    "code.py",
    "metar_source.py",
    "updater.py",
    "render.py",
    "wifi_setup.py",
    "captive_dns.py",
    "webui.py",
]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print("usage: python tools/make_manifest.py <version> [--no-sha]   e.g. 2.0.1")
        sys.exit(1)
    version = args[0]
    # --no-sha omits hashes: needed once to recover a device whose on-board
    # updater can't compute sha256 (it only crashes when the manifest has hashes).
    no_sha = "--no-sha" in sys.argv
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    files = []
    for name in DEVICE_FILES:
        path = os.path.join(root, name)
        if not os.path.exists(path):
            print("warning: %s not found, skipping" % name)
            continue
        with open(path, "rb") as f:
            data = f.read()
        entry = {"path": name, "size": len(data)}
        if not no_sha:
            entry["sha256"] = hashlib.sha256(data).hexdigest()
        files.append(entry)

    manifest = {"version": version, "files": files}
    out = os.path.join(root, "version.json")
    with open(out, "w", newline="\n") as f:  # force LF so it matches the repo blob
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print("wrote version.json  (v%s, %d files)" % (version, len(files)))


if __name__ == "__main__":
    main()
