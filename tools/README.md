# Tools

Standalone diagnostic and debugging scripts for use alongside `sim_broadcaster`.
These are development utilities — not part of the broadcast pipeline.

## `capture_gear.py`

Listens on the broadcast UDP port and prints every `GEAR` packet to stdout.
Useful for confirming that gear packets are arriving and checking `on_ground`,
`gear_handle`, and `gear_total_pct` values in real time.

```bash
python tools/capture_gear.py
```

Press **Ctrl+C** to stop.  A summary of received packet counts is printed on exit.

**Requires:** Nothing beyond the standard library — no SimConnect needed.
Run this on any machine on the same LAN (including a laptop or Pi) while
`sim_broadcaster.py` is running on the simulator PC.

---

## `engine_diag.py`

Connects directly to SimConnect and logs `STARTER`, `COMBUSTION`, and `RPM`
state transitions for all four engine slots in real time.  Used to verify that
SimConnect is delivering engine events at the expected times and to diagnose
timing issues in the haptic engine-start/stop detection logic.

```bash
python tools/engine_diag.py
```

Press **Ctrl+C** to stop.

**Requires:** SimConnect (must be installed in the active Python environment)
and MSFS must be running.  Run this on the simulator PC, not a remote device.
