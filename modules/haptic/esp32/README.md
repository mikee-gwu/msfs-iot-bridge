# Haptic Feedback Module — ESP32

Turns engine events and landings from MSFS into something you can feel. A bass shaker
bolted to your seat or floor panel reproduces mechanical events — the uneven starter
cranking, the first cylinder catching, RPM climbing to idle, the abrupt power-cut thud
on shutdown, and the jolt and roll-out of touchdown scaled to how hard you landed.
It's not audio. You feel it through the chair.

## Files

| File | Purpose |
|---|---|
| `generator.py` | MicroPython script — deploy this to the ESP32 as `main.py` |
| `secrets.py` | WiFi credentials — **never committed, create locally** (see below) |

## How it works

`generator.py` listens on UDP port 49000 for three packet types broadcast by
`sim_broadcaster`:

| Packet | Fields used | Purpose |
|---|---|---|
| `ENGINES` | `starter_1`, `combustion_1` | Engine-start and engine-stop sequences |
| `GEAR` | `on_ground` | Touchdown detection |
| `DYNAMICS` | `g_force`, `vertical_speed_fpm` | Scale landing intensity at touchdown |

All sequences drive GPIO14 as a PWM output in the **18–30 Hz range** — below audible
frequency. At those rates the shaker doesn't make sound, it just moves. The duty cycle
(0–1023) sets how hard the voice coil is driven; random jitter applied per-step gives
the motion an organic, uneven texture rather than a mechanical pulse.

### Engine start

Fires when `starter_1` goes true while `combustion_1` is false.

```
Engine start timeline
─────────────────────────────────────────────────────────────
 0 ms        starter cranking — low uneven roll  (18 Hz)
 280 ms      first cylinder fires — hard thud    (26 Hz, duty ~950)
 480 ms      sputtering catches × 2              (25 Hz, 27 Hz)
 920 ms      RPM climbing — frequency ramps up   (22 → 30 Hz)
 1420 ms     settled idle — sustained roll       (30 Hz, duty ~730)
 1770 ms     gentle tail-off
─────────────────────────────────────────────────────────────
```

### Engine stop

Fires when `combustion_1` drops to false.

```
Engine stop timeline
─────────────────────────────────────────────────────────────
 0 ms        power cut — hard abrupt thud        (29 Hz, duty ~820)
 190 ms      RPM decay — freq and duty wind down (28 → 18 Hz)
 840 ms      final prop spin fading to silence   (18 → 10 Hz)
─────────────────────────────────────────────────────────────
```

### Landing

Fires when `on_ground` transitions false → true. `DYNAMICS` packets continuously update
a snapshot of `g_force` and `vertical_speed_fpm`; at the moment of touchdown those values
determine intensity.

Intensity is the max of two independent estimates so that a landing that is hard by
either metric feels appropriately intense:

- **G-load:** 1.0 G → 0.0 intensity, 2.5 G → 1.0
- **Sink rate:** 50 fpm → 0.0, 800 fpm → 1.0

| Intensity | Category | Haptic |
|---|---|---|
| < 0.2 | Greaser | Single soft thud + brief wheel-roll whisper |
| 0.2 – 0.55 | Firm / normal | Double thud + moderate runway rumble |
| > 0.55 | Hard landing | Triple thud + heavy sustained vibration |

A 5-second cooldown suppresses re-triggers within the same landing roll.

## The shaker

**Douk Audio BS-1** — 50W tactile transducer. Unlike a speaker, it has no cone; instead
a weighted voice coil is suspended inside a sealed housing that you bolt directly to a
surface. When the coil moves, the whole housing moves with it, vibrating whatever it's
attached to. At the 18–30 Hz PWM rates used here, this lands squarely in the range your
body registers as physical rumble rather than sound.

At 20V through an ~8Ω voice coil the shaker draws around 2.5A at full duty — about 50W
peak. The PWM duty cycle in the haptic sequences sits mostly in the 30–90% range, so
average power is comfortably under that ceiling.

## Wiring

The IRF520 MOSFET module acts as a **low-side switch**. The ESP32 never powers the
shaker directly; it only controls the MOSFET gate using PWM on GPIO14.

When GPIO14 is HIGH, the MOSFET conducts and current flows from the 20V supply through
the shaker and into ground. By rapidly switching the MOSFET on and off, the ESP32
controls both vibration intensity (duty cycle) and vibration frequency.

### Wiring diagram

```text
                           CONTROL SIDE
                    (ESP32 → IRF520 Module)

      ESP32                           IRF520 Module
 ┌────────────────┐              ┌─────────────────┐
 │                │              │                 │
 │ GPIO14 ────────┼─────────────►│ SIG             │
 │ 3.3V   ────────┼─────────────►│ VCC             │
 │ GND    ────────┼─────────────►│ GND             │
 │                │              │                 │
 └────────────────┘              └─────────────────┘


                            POWER SIDE
                 (20V Supply → Shaker → MOSFET)

      +20V PSU
          │
          │
          ▼
    ┌───────────┐
    │ Douk BS-1 │
    │ Bass      │
    │ Shaker    │
    └─────┬─────┘
          │
          ▼
    ┌───────────┐
    │ DRAIN     │
    │  IRF520   │
    │ SOURCE    │
    └─────┬─────┘
          │
          ▼
        GND


                        COMMON GROUND

      PSU (-) ─────────────┐
                           │
      ESP32 GND ───────────┼──────── Shared Ground
                           │
      IRF520 GND ──────────┘
```

### Connection table

| From          | To             |
| ------------- | -------------- |
| ESP32 GPIO14  | IRF520 SIG     |
| ESP32 3.3V    | IRF520 VCC     |
| ESP32 GND     | IRF520 GND     |
| PSU +20V      | Shaker +       |
| Shaker −      | IRF520 DRAIN   |
| IRF520 SOURCE | PSU − (Ground) |
| PSU −         | ESP32 GND      |

### Current flow

When the MOSFET is ON:

```text
+20V PSU
    │
    ▼
Bass Shaker
    │
    ▼
IRF520 Drain
    │
    ▼
IRF520 Source
    │
    ▼
PSU Ground
```

When the MOSFET is OFF, current flow stops and the shaker becomes inactive.

### Notes

* All grounds must be connected together.
* The ESP32 only supplies the control signal; the shaker is powered entirely by the 20V supply.
* Keep the shaker power wiring reasonably short and use adequately sized wire for the current.
* The Douk Audio BS-1 is approximately 8Ω. At 20V it can draw about 2.5A at 100% duty cycle (roughly 50W peak).
* The haptic effects typically operate well below continuous full power because PWM duty cycle varies throughout each sequence.

## Wi-Fi credentials

Create `secrets.py` on the device (not in the repo — it's gitignored):

```python
SSID     = "YourNetworkName"
PASSWORD = "YourPassword"
```

## Deploying

Flash MicroPython to the ESP32 first:
[micropython.org/download/ESP32_GENERIC](https://micropython.org/download/ESP32_GENERIC/)

Then copy files using `mpremote`:

```bash
pip install mpremote
mpremote connect PORT fs cp secrets.py :secrets.py
mpremote connect PORT fs cp generator.py :main.py
```

`PORT` is your serial device (`/dev/tty.usbserial-*` on macOS, `COM*` on Windows).
Saving as `main.py` makes it run automatically on every boot.

Alternatively, Thonny works fine: set the interpreter to MicroPython (ESP32), open both
files, and save them to the device — saving `generator.py` as `main.py`.

## Parts

| Part | Link |
|---|---|
| ESP32 | https://amzn.to/4oDUoLI |
| IRF520 MOSFET module | https://amzn.to/4xIT8en |
| Douk Audio BS-1 bass shaker | https://amzn.to/4erWjhG |
| 20V DC power supply | https://amzn.to/3QxWz76 |
