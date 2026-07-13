# boot.py — runs ONCE at power-on, before code.py.
#
# Two safety-critical jobs, so this file stays tiny and depends only on core
# modules. It must keep working even if an OTA update breaks every other file:
#
#   1. Make the flash writable by CircuitPython, so the config UI can save
#      config.json and the updater can install files. Trade-off: while running,
#      the CIRCUITPY drive is read-only to a host computer. That's expected for
#      a finished unit — you only see a writable drive when you deliberately
#      update firmware.
#
#   2. Roll back a bad OTA update. If a freshly-installed update keeps crashing
#      before code.py can confirm it's healthy, restore the previous version
#      from the .bak files the updater left behind.

import microcontroller
import storage
import os

# --- NVM: a few bytes of storage that survive resets and power cycles --------
NVM_MAGIC = 0x4D        # 'M' sentinel: proves we initialized NVM
I_MAGIC = 0
I_PENDING = 1           # 1 = an OTA update is on trial, not yet confirmed healthy
I_CRASHES = 2           # boots since the update that didn't reach "healthy"
I_MAINT = 3             # 1 = one-shot: skip remount so the USB host can write
MAX_CRASHES = 3         # roll back after this many failed boots

nvm = microcontroller.nvm

# First-ever boot: initialize our NVM bytes.
if nvm[I_MAGIC] != NVM_MAGIC:
    nvm[I_MAGIC] = NVM_MAGIC
    nvm[I_PENDING] = 0
    nvm[I_CRASHES] = 0
    nvm[I_MAINT] = 0

# Maintenance mode (triggered from the web UI): keep the flash writable by the
# USB host for this one boot, so you can `circup install` libraries or edit
# files. One-shot — cleared now so the next reset returns to normal operation.
maintenance = (nvm[I_MAINT] == 1)
nvm[I_MAINT] = 0

# 1. Hand flash write-control to CircuitPython — unless we're doing USB
#    maintenance this boot, in which case the host keeps write access instead.
if maintenance:
    print("boot.py: MAINTENANCE MODE - USB drive writable; reset when done")
else:
    try:
        storage.remount("/", readonly=False)
    except RuntimeError:
        pass  # already writable, or a host owns it — carry on

# 2. Rollback watchdog. If an update is on trial, count this boot. code.py
#    clears I_PENDING once the map is running normally; if it never does and we
#    keep rebooting, restore the .bak files.
if nvm[I_PENDING] == 1:
    nvm[I_CRASHES] = nvm[I_CRASHES] + 1
    if nvm[I_CRASHES] >= MAX_CRASHES:
        print("boot.py: update failed %d boots, rolling back" % nvm[I_CRASHES])
        for bak in [f for f in os.listdir("/") if f.endswith(".bak")]:
            target = bak[:-4]  # strip ".bak"
            try:
                try:
                    os.remove(target)
                except OSError:
                    pass
                os.rename(bak, target)
                print("  restored " + target)
            except OSError as e:
                print("  rollback error for %s: %s" % (target, e))
        nvm[I_PENDING] = 0
        nvm[I_CRASHES] = 0
        print("boot.py: rollback complete")
