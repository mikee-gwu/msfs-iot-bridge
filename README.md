# sim_broadcaster

Polls Microsoft Flight Simulator (MSFS 2020 / 2024) or Prepar3D via
SimConnect and broadcasts live flight state as typed UDP JSON packets on the
local network subnet.

## Overview

Any device on the same LAN — an ESP32, a Raspberry Pi, a tablet, another PC —
can listen on a single UDP port and react to flight events in real time,
without any inbound firewall exceptions on the simulator PC, without a
subscription handshake, and without combining multiple packets to derive a
single value. On modern gigabit networks the overhead is minimal, and cheap
microcontrollers can easily listen for specific packet types without undue
processing overhead.

Packets are organised into **ten semantic groups**, each transmitted at a rate
matched to how fast that data actually changes.  Every packet is a
self-contained JSON object small enough to fit in a single Ethernet datagram
(under 1 472 bytes), so there is no IP fragmentation and no reassembly work on
the receiver side.

### Packet groups at a glance

| Type | Rate | Size | Purpose |
|---|---|---|---|
| `DYNAMICS` | 20 Hz | ~413 B | Attitude, G-load, body acceleration, stall / overspeed |
| `GEAR` | 10 Hz | ~403 B | Gear position, wheel RPM, brakes, ground contact |
| `SURFACES` | 10 Hz | ~502 B | Flaps, spoilers, control-surface deflection and trim |
| `ENGINES` | 10 Hz | ~873 B | Per-engine RPM, N1/N2, EGT, throttle, fuel flow |
| `POSITION` | 5 Hz | ~337 B | Lat/lon/alt, airspeeds, magnetic variation |
| `AUTOPILOT` | 5 Hz | ~506 B | AP mode flags and reference values |
| `ELECTRICAL` | 2 Hz | ~249 B | Bus voltages, current draw, switch states |
| `ENVIRONMENT` | 1 Hz | ~381 B | Wind, temperature, pressure, precipitation |
| `LIGHTS` | 1 Hz | ~145 B | Light switch states |
| `STATIC` | 0.1 Hz | ~531 B | Aircraft identity and design parameters |

Total broadcast bandwidth across all groups: **≈ 251 Kbps** (0.25 Mbps).

---

## Quick start

### 1 — Create a virtual environment and install dependencies

Open a terminal in the project folder and run:

```bat
python -m venv venv
venv\Scripts\activate
pip install SimConnect
```

> On first activation you will see `(venv)` prepended to your prompt.
> Run `venv\Scripts\activate` again in any new terminal you open.

### 2 — Start Microsoft Flight Simulator

Launch MSFS 2020 or 2024 and load any flight — the main menu is enough, but
data only begins flowing once a flight (or mission) is active and a world is
loaded.  SimConnect connects to the running sim process; the broadcaster will
retry automatically if the sim is not ready yet.

### 3 — Run the broadcaster

With the venv active and the sim running:

```bat
python sim_broadcaster.py
```

You should see output like:

```
SimConnect connected.
Broadcasting on 255.255.255.255:49000
```

The script keeps running until you press **Ctrl+C**.  UDP packets start
flowing immediately — no handshake, no pairing required.

### 4 — Verify packets with Wireshark

[Wireshark](https://www.wireshark.org/) lets you inspect the raw packets on
the wire without writing any receiver code.

1. **Open Wireshark** and select your active network adapter (usually
   labelled "Wi-Fi" or "Ethernet").
2. **Enter this display filter** in the filter bar and press Enter:

   ```
   udp.port == 49000
   ```

3. Packets will appear immediately.  Click any row to expand it; the JSON
   payload is visible in the "Data" section of the packet detail pane.

   If you changed the default port with `--port`, substitute that number in
   the filter (e.g. `udp.port == 49001`).

**Tip:** Use `Edit → Find Packet` (Ctrl+F) and search in "Packet bytes" for
`"DYNAMICS"` or `"GEAR"` to jump straight to a specific packet type.

---

## Requirements

- Windows 10/11
- Microsoft Flight Simulator 2020 or 2024
- Python 3.10 or later
- [`SimConnect`](https://pypi.org/project/SimConnect/) Python library

```
pip install SimConnect
```

The simulator must be **running** before `sim_broadcaster.py` is started.
SimConnect connects to the sim process directly; there is no network address
to configure on the sim side.

---

## Usage

```
python sim_broadcaster.py
```

Optional arguments:

| Flag | Default | Description |
|---|---|---|
| `--ip` | `255.255.255.255` | Destination IP.  `255.255.255.255` broadcasts to every device on the local subnet.  Pass a specific IP (e.g. `192.168.1.42`) to unicast to one device. |
| `--port` | `49000` | UDP destination port. |

```
python sim_broadcaster.py --port 49001
python sim_broadcaster.py --ip 192.168.1.255          # directed subnet broadcast
python sim_broadcaster.py --ip 192.168.1.42 --port 5000  # unicast to one device
```

Press **Ctrl+C** to stop cleanly.  SimConnect is disconnected and the socket
is closed before the process exits.

---

## Configuration

Open `sim_broadcaster.py` and edit the constants near the top of the file.

### Update rates

These are the default values — they are calibrated to the rate at which each
data group actually changes in practice, and are reasonable for most use cases.
Adjust them only if you have a specific reason (e.g. a slower IoT device that
can't keep up, or a motion platform that needs higher fidelity).

```python
INTERVALS: dict[str, float] = {
    "DYNAMICS":    1 / 20,   # 20 Hz — attitude, G-load, body acceleration
    "GEAR":        1 / 10,   # 10 Hz — gear position, wheel RPM, brakes
    "SURFACES":    1 / 10,   # 10 Hz — flaps, spoilers, control surface deflection
    "ENGINES":     1 / 10,   # 10 Hz — RPM, N1/N2, EGT, throttle, fuel flow
    "POSITION":    1 /  5,   #  5 Hz — lat/lon/alt, airspeeds
    "AUTOPILOT":   1 /  5,   #  5 Hz — AP modes and reference values
    "ELECTRICAL":  1 /  2,   #  2 Hz — bus voltages, battery load
    "ENVIRONMENT": 1.0,       #  1 Hz — wind, temperature, ambient pressure
    "LIGHTS":      1.0,       #  1 Hz — light switch states
    "STATIC":     10.0,       # 0.1 Hz — aircraft identity and config (rarely changes)
}
```

The main loop runs at the fastest configured rate (`DYNAMICS`).  All slower
groups are gated by elapsed time.  Raising `DYNAMICS` to 50 Hz is safe on a
modern machine but will not make gear/surface events perceptibly faster because
SimConnect itself caps its output at roughly 18 Hz for most variables.

### SimConnect cache

```python
SIM_CACHE_MS = 50
```

The SimConnect Python library caches each variable for this many milliseconds
before re-querying the simulator.  50 ms is appropriate for a 20 Hz loop.
Reducing it below 20 ms has no practical benefit and increases CPU overhead.

### Engine count

```python
NUM_ENGINES = 4
```

The `ENGINES` packet always contains four engine slots.  Receivers should
read `num_engines` to know how many slots are valid; the rest will be zero.
Engines are 1-indexed in the field names (`combustion_1`, `n1_pct_2`, etc.).

---

## Network architecture

### Why UDP broadcast?

- **No inbound connections** — the simulator PC never accepts a socket
  connection from an IoT device, so no firewall rule is needed.
- **Zero configuration** — a new receiver joins by binding the port; the
  broadcaster needs no knowledge of it.
- **Decoupled scaling** — additional receivers are free; the broadcaster
  sends exactly one packet per group per interval regardless of listener count.

### Packet loss

UDP provides no delivery guarantee.  On a switched gigabit LAN, loss is
effectively zero under normal conditions.  For haptics and motion platforms
this is fine: a dropped packet is simply a skipped frame.  Receivers that
need to detect gaps can use receive-time: a gap larger than
`2 × expected_interval` between consecutive packets of the same type means
one was dropped.

### Subnet broadcast vs directed broadcast

`255.255.255.255` is the limited broadcast address.  Routers do not forward
it, so it reaches only devices on the directly connected subnet.  If your
network is segmented, use the subnet's directed broadcast address instead
(e.g. `192.168.1.255` for a `/24` network).

---

## Packet protocol reference

All packets share one mandatory field:

| Field | Type | Description |
|---|---|---|
| `type` | `str` | Packet group name (e.g. `"DYNAMICS"`).  Filter on this. |

Field naming conventions used throughout:
- Suffix `_rad` → radians
- Suffix `_deg` → degrees
- Suffix `_ft` → feet
- Suffix `_m` → metres
- Suffix `_kt` → knots
- Suffix `_fps` → feet per second
- Suffix `_fpm` → feet per minute
- Suffix `_mps` → metres per second
- Suffix `_pct` → percent (0–100)
- Suffix `_a` → amperes
- Suffix `_mb` → millibars
- Suffix `_inhg` → inches of mercury
- Suffix `_lb` → pounds
- Suffix `_gal` → US gallons
- Suffix `_pph` → pounds per hour
- Suffix `_rpm` → revolutions per minute
- No suffix + integer value → boolean (0/1) or enum

---

### DYNAMICS — 20 Hz

Attitude, rotation rates, body-axis acceleration, G-load, and envelope
warnings.  Intended for motion platforms, haptic vests, and seat actuators.

| Field | Type | Unit | Notes |
|---|---|---|---|
| `pitch_rad` | float | rad | Nose-up positive |
| `bank_rad` | float | rad | Right-bank positive |
| `heading_mag_rad` | float | rad | Magnetic heading, 0–2π |
| `g_force` | float | G | 1.0 in level flight; negative in pushover |
| `accel_body_x` | float | ft/s² | Lateral: right positive |
| `accel_body_y` | float | ft/s² | Vertical: up positive |
| `accel_body_z` | float | ft/s² | Longitudinal: forward positive |
| `rot_vel_x` | float | ft/s | Roll rate about body X |
| `rot_vel_y` | float | ft/s | Yaw rate about body Y |
| `rot_vel_z` | float | ft/s | Pitch rate about body Z |
| `vertical_speed_fpm` | float | ft/min | Negative = descending |
| `stall_warning` | int | 0/1 | 1 = stall warning active |
| `overspeed_warning` | int | 0/1 | 1 = Vmo/Mmo exceeded |
| `incidence_alpha_rad` | float | rad | Angle of attack |
| `incidence_beta_rad` | float | rad | Sideslip angle |

---

### GEAR — 10 Hz

Landing gear position, wheel RPM, braking, and ground contact.  Designed for
touchdown haptics, wheel-spin rumble, and brake-pressure feedback devices.

| Field | Type | Unit | Notes |
|---|---|---|---|
| `on_ground` | int | 0/1 | 1 = at least one gear strut compressed |
| `gear_handle` | int | 0/1 | 1 = gear handle in DOWN position |
| `gear_total_pct` | float | 0–1 | Average extension across all gear |
| `gear_center_pct` | float | 0–1 | Center (nose) gear extension |
| `gear_left_pct` | float | 0–1 | Left main gear extension |
| `gear_right_pct` | float | 0–1 | Right main gear extension |
| `wheel_rpm_center` | float | RPM | Center wheel spin rate |
| `wheel_rpm_left` | float | RPM | Left main wheel spin rate |
| `wheel_rpm_right` | float | RPM | Right main wheel spin rate |
| `brake_left` | float | 0–1 | Left brake application (0 = none, 1 = full) |
| `brake_right` | float | 0–1 | Right brake application |
| `brake_parking` | int | 0/1 | 1 = parking brake set |
| `antiskid_active` | int | 0/1 | 1 = anti-skid system engaged |
| `gear_speed_exceeded` | int | 0/1 | 1 = Vlo exceeded with gear extended |
| `water_rudder_handle` | float | 0–1 | Float-plane water rudder (0 = retracted) |

---

### SURFACES — 10 Hz

Wing high-lift devices, spoilers, and primary control surface positions and
trims.  Useful for force-feedback yoke/pedal peripherals and flap-position
indicators.

| Field | Type | Unit | Notes |
|---|---|---|---|
| `flaps_handle_index` | int | index | Detent index (0 = up, max varies by aircraft) |
| `flaps_handle_pct` | float | 0–1 | Handle position as fraction of travel |
| `te_flaps_left_pct` | float | 0–1 | Left trailing-edge flap extension |
| `te_flaps_right_pct` | float | 0–1 | Right trailing-edge flap extension |
| `le_flaps_left_pct` | float | 0–1 | Left leading-edge flap / slat extension |
| `le_flaps_right_pct` | float | 0–1 | Right leading-edge flap / slat extension |
| `spoilers_armed` | int | 0/1 | 1 = auto-spoilers armed |
| `spoilers_handle_pct` | float | 0–1 | Spoiler handle position |
| `spoilers_left_pct` | float | 0–1 | Left spoiler panel extension |
| `spoilers_right_pct` | float | 0–1 | Right spoiler panel extension |
| `elevator_pos` | float | −1–1 | Elevator input; +1 = full nose-up |
| `aileron_pos` | float | −1–1 | Aileron input; +1 = right roll |
| `rudder_pos` | float | −1–1 | Rudder input; +1 = right yaw |
| `elevator_trim_rad` | float | rad | Elevator trim deflection |
| `aileron_trim_pct` | float | 0–1 | Aileron trim (0 = neutral) |
| `rudder_trim_pct` | float | 0–1 | Rudder trim (0 = neutral) |

---

### ENGINES — 10 Hz

Per-engine state for up to four engines.  Fields are suffixed `_1` through
`_4`.  Read `num_engines` to know how many suffixes contain valid data; unused
slots are zero.

| Field | Type | Unit | Notes |
|---|---|---|---|
| `num_engines` | int | — | Number of engines on this aircraft (1–4) |
| `engine_type` | int | enum | 0=Piston, 1=Jet, 2=None, 3=Helo turbine, 5=Turboprop |
| `combustion_N` | int | 0/1 | 1 = engine N is running |
| `starter_N` | int | 0/1 | 1 = starter motor engaged (fires before combustion on start) |
| `throttle_pct_N` | float | 0–100 | Throttle lever position percent |
| `rpm_N` | float | RPM | Engine RPM (all types) |
| `n1_pct_N` | float | % | Turbine N1 (0 for piston engines) |
| `n2_pct_N` | float | % | Turbine N2 (0 for piston engines) |
| `egt_rankine_N` | float | °R | Exhaust gas temperature in Rankine |
| `oil_temp_rankine_N` | float | °R | Oil temperature in Rankine |
| `fuel_flow_pph_N` | float | lb/hr | Turbine fuel flow (0 for piston) |
| `on_fire_N` | int | 0/1 | 1 = engine N is on fire |

> **Unit note:** SimConnect returns EGT and oil temperature in Rankine.
> To convert to Celsius: `C = (R - 491.67) × 5/9`.
> To convert to Fahrenheit: `F = R - 459.67`.

---

### POSITION — 5 Hz

Geographic position, altitude, and airspeed data.  Useful for moving-map
displays, altitude alerters, and approach warning systems.

| Field | Type | Unit | Notes |
|---|---|---|---|
| `lat_deg` | float | °N | Latitude; north positive |
| `lon_deg` | float | °E | Longitude; east positive |
| `altitude_ft` | float | ft | MSL altitude |
| `alt_agl_ft` | float | ft | Altitude above ground level |
| `ground_altitude_m` | float | m | Terrain elevation at aircraft position |
| `airspeed_indicated_kt` | float | kt | IAS (what the cockpit ASI reads) |
| `airspeed_true_kt` | float | kt | TAS |
| `airspeed_mach` | float | M | Current Mach number |
| `ground_velocity_kt` | float | kt | Speed over the ground |
| `pressure_altitude_m` | float | m | Pressure altitude |
| `magvar_deg` | float | ° | Magnetic variation at current position |

---

### AUTOPILOT — 5 Hz

Autopilot engagement state and reference values.  All `_lock` / `_hold`
fields are booleans (0/1).

| Field | Type | Unit | Notes |
|---|---|---|---|
| `ap_master` | int | 0/1 | Master AP switch |
| `wing_leveler` | int | 0/1 | Wing leveler active |
| `nav1_lock` | int | 0/1 | NAV1 lateral mode |
| `heading_lock` | int | 0/1 | HDG mode active |
| `heading_lock_dir_deg` | float | ° | Selected heading bug |
| `altitude_lock` | int | 0/1 | ALT hold active |
| `altitude_lock_var_ft` | float | ft | Selected altitude |
| `vs_hold` | int | 0/1 | Vertical speed mode active |
| `vs_hold_var_fpm` | float | ft/min | Selected V/S |
| `airspeed_hold` | int | 0/1 | IAS hold active |
| `airspeed_hold_var_kt` | float | kt | Selected IAS |
| `mach_hold` | int | 0/1 | Mach hold active |
| `mach_hold_var` | float | M | Selected Mach |
| `approach_hold` | int | 0/1 | Approach mode active |
| `glideslope_hold` | int | 0/1 | GS mode active |
| `backcourse_hold` | int | 0/1 | Back-course mode active |
| `autothrottle_active` | int | 0/1 | Autothrottle engaged |
| `fd_active` | int | 0/1 | Flight director active |
| `fd_pitch_rad` | float | rad | Flight director pitch command |
| `fd_bank_rad` | float | rad | Flight director bank command |
| `flight_level_change` | int | 0/1 | FLC / speed-on-pitch mode |
| `attitude_hold` | int | 0/1 | Pitch attitude hold |

---

### ELECTRICAL — 2 Hz

Electrical system health.  Useful for avionics-failure simulators and
cockpit-builder panel illumination.

| Field | Type | Unit | Notes |
|---|---|---|---|
| `master_battery` | int | 0/1 | Battery master switch |
| `avionics_master` | int | 0/1 | Avionics master switch |
| `main_bus_voltage` | float | V | Main electrical bus voltage |
| `battery_voltage` | float | V | Battery terminal voltage |
| `battery_load_a` | float | A | Current drawn from battery |
| `total_load_a` | float | A | Total electrical load |
| `avionics_bus_voltage` | float | V | Avionics bus voltage |
| `main_bus_amps` | float | A | Main bus current |

---

### ENVIRONMENT — 1 Hz

Ambient atmospheric conditions around the aircraft.

| Field | Type | Unit | Notes |
|---|---|---|---|
| `ambient_temp_c` | float | °C | OAT at aircraft altitude |
| `ambient_pressure_inhg` | float | inHg | Ambient static pressure |
| `wind_velocity_kt` | float | kt | Wind speed |
| `wind_direction_deg` | float | ° | Wind direction (from, magnetic) |
| `wind_x_mps` | float | m/s | Wind east/west component |
| `wind_y_mps` | float | m/s | Wind vertical component |
| `wind_z_mps` | float | m/s | Wind north/south component |
| `barometer_mb` | float | mbar | Barometric pressure (altimeter setting) |
| `sea_level_pressure_mb` | float | mbar | ISA sea-level pressure |
| `total_air_temp_c` | float | °C | TAT (includes ram rise) |
| `in_cloud` | int | 0/1 | 1 = aircraft inside cloud |
| `visibility_m` | float | m | Ambient visibility |
| `precip_state` | int | mask | Precipitation bitmask (sim-defined) |

---

### LIGHTS — 1 Hz

External and internal light switch states.  `light_states_mask` encodes all
lights as a bitmask; individual fields are provided for convenience.

| Field | Type | Notes |
|---|---|---|
| `light_states_mask` | int | Bit 0=Nav, 1=Beacon, 2=Landing, 3=Taxi, 4=Strobe, 5=Panel, 6=Recognition, 7=Wing, 8=Logo, 9=Cabin |
| `strobe` | int | 0/1 |
| `landing` | int | 0/1 |
| `taxi` | int | 0/1 |
| `beacon` | int | 0/1 |
| `nav` | int | 0/1 |
| `panel` | int | 0/1 |
| `logo` | int | 0/1 |
| `wing` | int | 0/1 |
| `cabin` | int | 0/1 |

---

### STATIC — 0.1 Hz (every 10 s)

Aircraft identity and fixed design parameters.  These values do not change
during a flight.  Receivers should cache the most recently received STATIC
packet and use it to contextualise data from other groups (e.g. whether gear
is retractable, how many engines to expect).

| Field | Type | Notes |
|---|---|---|
| `title` | str | Full aircraft name from `aircraft.cfg` |
| `atc_type` | str | ICAO type designator (e.g. `"A320"`) |
| `atc_model` | str | ATC model string |
| `atc_id` | str | Tail number / registration |
| `atc_airline` | str | Airline name |
| `atc_flight_number` | str | Flight number |
| `num_engines` | int | 1–4 |
| `engine_type` | int | 0=Piston, 1=Jet, 2=None, 3=Helo turbine, 5=Turboprop |
| `is_gear_retractable` | int | 0/1 |
| `is_tail_dragger` | int | 0/1 |
| `wing_span_ft` | float | ft |
| `wing_area_sqft` | float | ft² |
| `empty_weight_lb` | float | lb |
| `max_gross_weight_lb` | float | lb |
| `fuel_total_capacity_gal` | float | US gallons |
| `design_speed_vs0_fps` | float | ft/s — stall speed flaps full |
| `design_speed_vs1_fps` | float | ft/s — stall speed flaps up |
| `design_speed_vc_fps` | float | ft/s — design cruise speed |
| `typical_descent_rate_fpm` | float | ft/min |

---

## IoT receiver examples

### Python (PC / Raspberry Pi)

```python
import socket
import json

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("", 49000))

while True:
    data, _ = sock.recvfrom(4096)
    packet = json.loads(data)

    if packet["type"] == "GEAR":
        if packet["on_ground"] and packet["wheel_rpm_left"] > 100:
            print("Touchdown detected — wheels spinning")

    elif packet["type"] == "DYNAMICS":
        g = packet["g_force"]
        if abs(g) > 2.0:
            print(f"High G-load: {g:.2f} G")
```

### MicroPython (ESP32 / Pico W)

```python
import socket
import json
import network

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect("YourSSID", "YourPassword")
while not wlan.isconnected():
    pass

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("", 49000))

# Only care about GEAR and DYNAMICS — everything else is discarded cheaply
while True:
    data, _ = sock.recvfrom(2048)
    packet = json.loads(data)
    ptype = packet["type"]

    if ptype == "GEAR":
        on_ground = packet["on_ground"]
        gear_pct  = packet["gear_total_pct"]
        if on_ground and gear_pct > 0.99:
            # drive a rumble motor proportional to wheel spin
            intensity = min(packet["wheel_rpm_left"] / 500.0, 1.0)
            set_motor_pwm(intensity)

    elif ptype == "DYNAMICS":
        # map G-force to haptic seat intensity
        excess_g = max(0.0, abs(packet["g_force"]) - 1.0)
        set_seat_haptic(min(excess_g / 3.0, 1.0))
```

### Arduino / ESP32 (C++)

Requires [ArduinoJson](https://arduinojson.org/) v6 or later.

```cpp
#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>

WiFiUDP udp;

void setup() {
    WiFi.begin("YourSSID", "YourPassword");
    while (WiFi.status() != WL_CONNECTED) delay(500);
    udp.begin(49000);
}

void loop() {
    int size = udp.parsePacket();
    if (size == 0) return;

    // Buffer sized for the largest expected packet (ENGINES ~873 B)
    char buf[1024];
    int len = udp.read(buf, sizeof(buf) - 1);
    buf[len] = '\0';

    StaticJsonDocument<1024> doc;
    if (deserializeJson(doc, buf) != DeserializationError::Ok) return;

    const char* type = doc["type"];

    if (strcmp(type, "GEAR") == 0) {
        bool   onGround  = doc["on_ground"];
        float  gearPct   = doc["gear_total_pct"];
        float  wheelRpm  = doc["wheel_rpm_left"];
        bool   parking   = doc["brake_parking"];

        if (onGround && wheelRpm > 50.0f) {
            // touchdown — drive haptic motor
            int pwm = constrain((int)(wheelRpm / 5.0f), 0, 255);
            analogWrite(HAPTIC_PIN, pwm);
        }
    }

    else if (strcmp(type, "DYNAMICS") == 0) {
        float g     = doc["g_force"];
        float pitch = doc["pitch_rad"];
        float bank  = doc["bank_rad"];
        // drive seat actuators, etc.
    }

    else if (strcmp(type, "STATIC") == 0) {
        // cache aircraft config for use in other handlers
        strncpy(aircraftTitle, doc["title"] | "", sizeof(aircraftTitle));
        numEngines      = doc["num_engines"] | 1;
        gearRetractable = doc["is_gear_retractable"] | 0;
    }
}
```

---

## Architecture notes

### Why the engine indexed-variable workaround?

The SimConnect Python library maintains a single `Request` object per indexed
variable template (e.g. `GENERAL ENG RPM:index`).  Calling
`aq.get("GENERAL_ENG_RPM:1")` and then `aq.get("GENERAL_ENG_RPM:2")` on the
same `AircraftRequests` instance mutates the same underlying object via
`setIndex()`, which rewrites the SimConnect data definition and triggers a
re-registration (`redefine()`).  You cannot hold two different indices live at
the same time through this API.

`sim_broadcaster.py` works around this by constructing one `Request` object
per `(variable, engine)` pair with the index baked into the SimConnect
variable name at construction time (e.g. `b"GENERAL ENG RPM:2"`).  These
objects bypass the `AircraftRequests` cache and register directly with the
SimConnect DLL.  They are completely independent and can be polled
simultaneously.

### Adding a new variable to an existing group

1. Find the SimConnect variable name in the
   [MSFS SDK documentation](https://docs.flightsimulator.com/html/Programming_Tools/SimVars/Simulation_Variables.htm)
   or in `venv/Lib/site-packages/SimConnect/RequestList.py`.
2. Add a line to the relevant `build_*` function using `_f()`, `_i()`, or
   `_s()`.
3. Verify the packet still fits under 1 472 bytes by running:
   ```
   python - <<'EOF'
   import json
   # paste a sample packet dict here and check len(json.dumps(it))
   EOF
   ```

### Adding a new packet group

1. Add an entry to `INTERVALS` with the desired rate.
2. Write a `build_mygroup(aq)` function returning a dict with a `"type"` field.
3. Add the lambda to the `builders` dict inside `run()`.

---

## Troubleshooting

**`SimConnect` import fails**
> `pip install SimConnect` — the package must be installed in the same Python
> environment you are using to run the script.

**`Exception: SimConnect: connection failed`**
> The simulator is not running, or SimConnect is disabled.  Start MSFS / P3D
> before running the broadcaster.  For P3D, ensure SimConnect is enabled in
> the add-on XML configuration.

**No packets received on the IoT device**
> 1. Confirm both machines are on the same subnet and that no managed switch
>    is filtering broadcast traffic.
> 2. Try a directed broadcast to the subnet address (e.g. `--ip 192.168.1.255`)
>    instead of `255.255.255.255`.
> 3. Check that the Windows Defender firewall is not blocking **outbound** UDP
>    on port 49000 from `python.exe` (outbound rules are usually open by
>    default, but corporate images sometimes restrict them).

**Packets arrive but values are all zero**
> SimConnect returns `None` for variables it cannot provide (e.g. turbine N1
> on a piston aircraft, or gear position on an aircraft without retractable
> gear).  The `_f()` / `_i()` helpers convert `None` to `0.0` / `0`.  This
> is expected — use the `STATIC` packet's `engine_type` and
> `is_gear_retractable` fields to contextualise zero values.

**High CPU usage**
> Lower the `DYNAMICS` rate or raise `SIM_CACHE_MS`.  The broadcaster polls
> SimConnect on every loop iteration; reducing the loop rate directly reduces
> CPU overhead.

**`ENGINES` packet is missing fields for engines 3 and 4**
> Only `num_engines` engines are active; slots for inactive engines contain
> zeros.  This is normal.

---

## Haptic Feedback (ESP32 Bass Shaker)

The `modules/haptic/esp32/generator.py` script runs on an ESP32 microcontroller
connected to a bass shaker (motion actuator) and listens for flight events via
the UDP packets from `sim_broadcaster`.

### Haptic feedback events

**Engine Startup** (`engine_start`)
- **Trigger**: Starter engages on a cold engine (starter transitions 0→1, engine not running)
- **Sequence**: Starter motor crank (uneven rumble) → ignition catch thuds → RPM climb → idle settle
- **Duration**: ~2.3 seconds
- **Feel**: Realistic multi-stage startup sequence from cold crank to smooth idle

**Engine Shutdown** (`engine_stop`)
- **Trigger**: Engine transitions from running to off (combustion goes 1→0)
- **Sequence**: Abrupt power loss thud → RPM wind-down → final prop spin fade
- **Duration**: ~1.1 seconds
- **Feel**: Power loss impact followed by descending pitch and intensity

**Landing Impact** (`landing_haptic`)
- **Trigger**: Aircraft touches down (on_ground transitions 0→1) or lands with high impact during baseline phase
- **Intensity scaling**: G-load and vertical speed determine impact severity
  - Soft landing (< 0.2 intensity): single soft thud + gentle wheel-roll rumble
  - Firm landing (0.2–0.55): double thuds + moderate runway vibration
  - Hard landing (> 0.55): triple thuds + sustained heavy vibration
- **Duration**: 0.5–1.5 seconds depending on landing severity
- **Feel**: Intensity matches landing violence from smooth touchdown to hard impact

### Hardware setup

```
ESP32 microcontroller
    ↓ GPIO14 (PWM output)
    ↓
MOSFET gate (signal input)
    ↓ drain
    ↓
Bass shaker / motion actuator
    ↓
Ground (common with ESP32)
```

### Tuning

Haptic parameters (frequency, duty cycle, durations, intensity curves) are
defined in `generator.py` and can be adjusted to match your hardware response
or personal preference. Key functions:
- `engine_start()` — startup sequence timing and frequencies
- `engine_stop()` — shutdown sequence
- `landing_haptic(peak_g, touchdown_vs_fpm)` — impact scaling curves
- `ramp()`, `roll()`, `thud()` — primitive haptic waveforms
