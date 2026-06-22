#!/usr/bin/env python3
"""Diagnostic: track engine start/stop transitions across multiple SimVars."""

import time
from SimConnect import SimConnect
from SimConnect.RequestList import Request

NUM_ENGINES = 4
POLL_INTERVAL = 0.05   # 20 Hz
RPM_START_THRESHOLD = 50   # RPM above which we consider "cranking"


def make_req(var, engine, sm):
    unit = b"Bool" if "COMBUSTION" in var or "STARTER" in var else b"Rpm"
    return Request((f"{var}:{engine}".encode(), unit), sm, _time=50)


def main():
    print("Connecting to SimConnect ...")
    sm = SimConnect()
    print("Connected. Watching starter / RPM / combustion — Ctrl+C to stop.\n")
    print(f"{'t+':>8}  {'ENG':>3}  {'VAR':<20}  EVENT")
    print("-" * 55)

    reqs = {}
    for n in range(1, NUM_ENGINES + 1):
        reqs[n] = {
            "STARTER":    make_req("GENERAL ENG STARTER",    n, sm),
            "RPM":        make_req("GENERAL ENG RPM",        n, sm),
            "COMBUSTION": make_req("GENERAL ENG COMBUSTION", n, sm),
        }

    prev = {n: {"STARTER": None, "RPM": None, "COMBUSTION": None}
            for n in range(1, NUM_ENGINES + 1)}
    # Coarse RPM state: False = below threshold, True = above threshold
    rpm_state = {n: None for n in range(1, NUM_ENGINES + 1)}

    t0 = time.monotonic()

    try:
        while True:
            for n in range(1, NUM_ENGINES + 1):
                elapsed = time.monotonic() - t0

                # --- STARTER (boolean) ---
                v = reqs[n]["STARTER"].value
                if v is not None:
                    state = bool(int(v))
                    if state != prev[n]["STARTER"]:
                        if prev[n]["STARTER"] is None:
                            print(f"t+{elapsed:6.2f}s  ENG {n}  {'STARTER':<20}  initial {'ON' if state else 'OFF'}")
                        else:
                            print(f"t+{elapsed:6.2f}s  ENG {n}  {'STARTER':<20}  *** {'ENGAGED' if state else 'DISENGAGED'} ***")
                        prev[n]["STARTER"] = state

                # --- RPM (threshold crossing) ---
                v = reqs[n]["RPM"].value
                if v is not None:
                    rpm = float(v)
                    new_rpm_state = rpm >= RPM_START_THRESHOLD
                    if prev[n]["RPM"] is None:
                        prev[n]["RPM"] = rpm
                        rpm_state[n] = new_rpm_state
                        print(f"t+{elapsed:6.2f}s  ENG {n}  {'RPM':<20}  initial {rpm:.0f} rpm")
                    else:
                        if new_rpm_state != rpm_state[n]:
                            label = f"CRANKING ({rpm:.0f} rpm)" if new_rpm_state else f"STOPPED ({rpm:.0f} rpm)"
                            print(f"t+{elapsed:6.2f}s  ENG {n}  {'RPM':<20}  *** {label} ***")
                            rpm_state[n] = new_rpm_state
                        prev[n]["RPM"] = rpm

                # --- COMBUSTION (boolean) ---
                v = reqs[n]["COMBUSTION"].value
                if v is not None:
                    state = bool(int(v))
                    if state != prev[n]["COMBUSTION"]:
                        if prev[n]["COMBUSTION"] is None:
                            print(f"t+{elapsed:6.2f}s  ENG {n}  {'COMBUSTION':<20}  initial {'ON' if state else 'OFF'}")
                        else:
                            print(f"t+{elapsed:6.2f}s  ENG {n}  {'COMBUSTION':<20}  *** {'START' if state else 'STOP'} ***")
                        prev[n]["COMBUSTION"] = state

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sm.exit()


if __name__ == "__main__":
    main()
