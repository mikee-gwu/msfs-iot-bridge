#!/usr/bin/env python3
"""
sim_broadcaster.py

Polls Microsoft Flight Simulator (MSFS 2020/2024) or Prepar3D via SimConnect
and broadcasts live flight state as typed UDP JSON packets on the local subnet.

Packets are organised into ten semantic groups, each sent at a rate matched to
how fast that data actually changes.  Every packet is a self-contained JSON
object that fits inside a single Ethernet UDP datagram (< 1 472 bytes), so
there is no IP fragmentation and no reassembly burden on the receiver.

Any IoT device on the same network can bind to UDP port 49000, filter by the
"type" field, and act on the fields it cares about — no inbound firewall
exception, no subscription handshake, no cross-packet math.

Usage
-----
    python sim_broadcaster.py
    python sim_broadcaster.py --port 49001
    python sim_broadcaster.py --ip 192.168.1.255   # directed subnet broadcast

See README.md for the full packet protocol reference and receiver examples.
"""

import argparse
import json
import logging
import socket
import time

from SimConnect import SimConnect, AircraftRequests
from SimConnect.RequestList import Request

# ---------------------------------------------------------------------------
# Configuration — edit these or pass as CLI arguments
# ---------------------------------------------------------------------------

DEFAULT_IP   = "255.255.255.255"   # subnet broadcast; use a specific IP for unicast
DEFAULT_PORT = 49000
NUM_ENGINES  = 4                   # max engines polled per ENGINES packet (1–4)
SIM_CACHE_MS = 50                  # SimConnect request cache lifetime in ms

# Send interval in seconds for each packet group.
# The main loop runs at the DYNAMICS rate; all other groups are gated by
# elapsed time since their last send.
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

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SimConnect value helpers
# ---------------------------------------------------------------------------

def _f(aq: AircraftRequests, name: str, default: float = 0.0) -> float:
    """Return a SimConnect variable as float, or *default* on None / error."""
    try:
        v = aq.get(name)
        return float(v) if v is not None else default
    except Exception:
        return default


def _i(aq: AircraftRequests, name: str, default: int = 0) -> int:
    """Return a SimConnect variable as int (booleans, enums, masks)."""
    return int(_f(aq, name, float(default)))


def _s(aq: AircraftRequests, name: str, default: str = "") -> str:
    """Return a SimConnect string variable, decoded to str."""
    try:
        v = aq.get(name)
        if v is None:
            return default
        if isinstance(v, (bytes, bytearray)):
            return v.decode("utf-8", errors="replace").rstrip("\x00")
        return str(v).rstrip("\x00")
    except Exception:
        return default


def _rv(req: Request, default: float = 0.0) -> float:
    """Return the value of a directly-constructed Request object."""
    try:
        v = req.value
        return float(v) if v is not None else default
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Engine indexed Request objects
# ---------------------------------------------------------------------------
# The SimConnect Python library stores a single Request per indexed variable
# template (e.g. GENERAL ENG RPM:index).  Calling aq.get("GENERAL_ENG_RPM:1")
# and aq.get("GENERAL_ENG_RPM:2") within the same AircraftRequests instance
# would mutate the same underlying Request object (setIndex rewrites the
# SimConnect definition), making it impossible to have two live indices at once.
#
# We work around this by constructing one Request per (variable, engine-index)
# pair with the index baked into the SimConnect variable name at construction
# time.  These Request objects are registered directly with the SimConnect DLL
# and are completely independent of the AircraftRequests hierarchy.

def _build_engine_requests(sm: SimConnect) -> list[dict]:
    """
    Return a list of per-engine Request dicts, index 0 = engine 1.
    Variables that do not apply to a given engine type (e.g. N1/N2 on a
    piston) will return None from SimConnect; _rv() substitutes 0.0.
    """
    reqs = []
    for n in range(1, NUM_ENGINES + 1):
        idx = str(n).encode()
        reqs.append({
            "combustion": Request((b"GENERAL ENG COMBUSTION:"               + idx, b"Bool"),            sm, _time=SIM_CACHE_MS),
            "throttle":   Request((b"GENERAL ENG THROTTLE LEVER POSITION:"  + idx, b"Percent"),         sm, _time=SIM_CACHE_MS),
            "rpm":        Request((b"GENERAL ENG RPM:"                       + idx, b"Rpm"),             sm, _time=SIM_CACHE_MS),
            "n1":         Request((b"TURB ENG N1:"                           + idx, b"Percent"),         sm, _time=SIM_CACHE_MS),
            "n2":         Request((b"TURB ENG N2:"                           + idx, b"Percent"),         sm, _time=SIM_CACHE_MS),
            "egt":        Request((b"GENERAL ENG EXHAUST GAS TEMPERATURE:"   + idx, b"Rankine"),         sm, _time=SIM_CACHE_MS),
            "oil_temp":   Request((b"GENERAL ENG OIL TEMPERATURE:"           + idx, b"Rankine"),         sm, _time=SIM_CACHE_MS),
            "fuel_flow":  Request((b"TURB ENG FUEL FLOW PPH:"                + idx, b"Pounds per hour"), sm, _time=SIM_CACHE_MS),
            "on_fire":    Request((b"ENG ON FIRE:"                           + idx, b"Bool"),            sm, _time=SIM_CACHE_MS),
        })
    return reqs


# ---------------------------------------------------------------------------
# Packet builders — one function per group
# ---------------------------------------------------------------------------

def build_dynamics(aq: AircraftRequests) -> dict:
    """20 Hz — attitude, rotation, acceleration, G-load, stall/overspeed."""
    return {
        "type":               "DYNAMICS",
        "pitch_rad":          _f(aq, "PLANE_PITCH_DEGREES"),
        "bank_rad":           _f(aq, "PLANE_BANK_DEGREES"),
        "heading_mag_rad":    _f(aq, "PLANE_HEADING_DEGREES_MAGNETIC"),
        "g_force":            _f(aq, "G_FORCE"),
        "accel_body_x":       _f(aq, "ACCELERATION_BODY_X"),
        "accel_body_y":       _f(aq, "ACCELERATION_BODY_Y"),
        "accel_body_z":       _f(aq, "ACCELERATION_BODY_Z"),
        "rot_vel_x":          _f(aq, "ROTATION_VELOCITY_BODY_X"),
        "rot_vel_y":          _f(aq, "ROTATION_VELOCITY_BODY_Y"),
        "rot_vel_z":          _f(aq, "ROTATION_VELOCITY_BODY_Z"),
        "vertical_speed_fpm": _f(aq, "VERTICAL_SPEED"),
        "stall_warning":      _i(aq, "STALL_WARNING"),
        "overspeed_warning":  _i(aq, "OVERSPEED_WARNING"),
        "incidence_alpha_rad":_f(aq, "INCIDENCE_ALPHA"),
        "incidence_beta_rad": _f(aq, "INCIDENCE_BETA"),
    }


def build_gear(aq: AircraftRequests) -> dict:
    """10 Hz — gear position, wheel RPM, brakes, ground contact."""
    return {
        "type":                "GEAR",
        "on_ground":           _i(aq, "SIM_ON_GROUND"),
        "gear_handle":         _i(aq, "GEAR_HANDLE_POSITION"),
        "gear_total_pct":      _f(aq, "GEAR_TOTAL_PCT_EXTENDED"),
        "gear_center_pct":     _f(aq, "GEAR_CENTER_POSITION"),
        "gear_left_pct":       _f(aq, "GEAR_LEFT_POSITION"),
        "gear_right_pct":      _f(aq, "GEAR_RIGHT_POSITION"),
        "wheel_rpm_center":    _f(aq, "CENTER_WHEEL_RPM"),
        "wheel_rpm_left":      _f(aq, "LEFT_WHEEL_RPM"),
        "wheel_rpm_right":     _f(aq, "RIGHT_WHEEL_RPM"),
        "brake_left":          _f(aq, "BRAKE_LEFT_POSITION"),
        "brake_right":         _f(aq, "BRAKE_RIGHT_POSITION"),
        "brake_parking":       _i(aq, "BRAKE_PARKING_POSITION"),
        "antiskid_active":     _i(aq, "ANTISKID_BRAKES_ACTIVE"),
        "gear_speed_exceeded": _i(aq, "GEAR_SPEED_EXCEEDED"),
        "water_rudder_handle": _f(aq, "WATER_RUDDER_HANDLE_POSITION"),
    }


def build_surfaces(aq: AircraftRequests) -> dict:
    """10 Hz — flaps, spoilers, control surface positions and trims."""
    return {
        "type":                "SURFACES",
        "flaps_handle_index":  _i(aq, "FLAPS_HANDLE_INDEX"),
        "flaps_handle_pct":    _f(aq, "FLAPS_HANDLE_PERCENT"),
        "te_flaps_left_pct":   _f(aq, "TRAILING_EDGE_FLAPS_LEFT_PERCENT"),
        "te_flaps_right_pct":  _f(aq, "TRAILING_EDGE_FLAPS_RIGHT_PERCENT"),
        "le_flaps_left_pct":   _f(aq, "LEADING_EDGE_FLAPS_LEFT_PERCENT"),
        "le_flaps_right_pct":  _f(aq, "LEADING_EDGE_FLAPS_RIGHT_PERCENT"),
        "spoilers_armed":      _i(aq, "SPOILERS_ARMED"),
        "spoilers_handle_pct": _f(aq, "SPOILERS_HANDLE_POSITION"),
        "spoilers_left_pct":   _f(aq, "SPOILERS_LEFT_POSITION"),
        "spoilers_right_pct":  _f(aq, "SPOILERS_RIGHT_POSITION"),
        "elevator_pos":        _f(aq, "ELEVATOR_POSITION"),
        "aileron_pos":         _f(aq, "AILERON_POSITION"),
        "rudder_pos":          _f(aq, "RUDDER_POSITION"),
        "elevator_trim_rad":   _f(aq, "ELEVATOR_TRIM_POSITION"),
        "aileron_trim_pct":    _f(aq, "AILERON_TRIM_PCT"),
        "rudder_trim_pct":     _f(aq, "RUDDER_TRIM_PCT"),
    }


def build_engines(aq: AircraftRequests, eng_reqs: list[dict]) -> dict:
    """10 Hz — per-engine RPM, N1/N2, EGT, throttle, fuel flow. Always
    contains four engine slots; use num_engines to know how many are valid."""
    packet: dict = {
        "type":        "ENGINES",
        "num_engines": _i(aq, "NUMBER_OF_ENGINES"),
        "engine_type": _i(aq, "ENGINE_TYPE"),
    }
    for i, eng in enumerate(eng_reqs, start=1):
        s = f"_{i}"
        packet[f"combustion{s}"]    = int(_rv(eng["combustion"]))
        packet[f"throttle_pct{s}"]  = _rv(eng["throttle"])
        packet[f"rpm{s}"]           = _rv(eng["rpm"])
        packet[f"n1_pct{s}"]        = _rv(eng["n1"])
        packet[f"n2_pct{s}"]        = _rv(eng["n2"])
        packet[f"egt_rankine{s}"]   = _rv(eng["egt"])
        packet[f"oil_temp_rankine{s}"] = _rv(eng["oil_temp"])
        packet[f"fuel_flow_pph{s}"] = _rv(eng["fuel_flow"])
        packet[f"on_fire{s}"]       = int(_rv(eng["on_fire"]))
    return packet


def build_position(aq: AircraftRequests) -> dict:
    """5 Hz — geographic position, altitude, airspeeds, magnetic variation."""
    return {
        "type":                  "POSITION",
        "lat_deg":               _f(aq, "PLANE_LATITUDE"),
        "lon_deg":               _f(aq, "PLANE_LONGITUDE"),
        "altitude_ft":           _f(aq, "PLANE_ALTITUDE"),
        "alt_agl_ft":            _f(aq, "PLANE_ALT_ABOVE_GROUND"),
        "ground_altitude_m":     _f(aq, "GROUND_ALTITUDE"),
        "airspeed_indicated_kt": _f(aq, "AIRSPEED_INDICATED"),
        "airspeed_true_kt":      _f(aq, "AIRSPEED_TRUE"),
        "airspeed_mach":         _f(aq, "AIRSPEED_MACH"),
        "ground_velocity_kt":    _f(aq, "GROUND_VELOCITY"),
        "pressure_altitude_m":   _f(aq, "PRESSURE_ALTITUDE"),
        "magvar_deg":            _f(aq, "MAGVAR"),
    }


def build_autopilot(aq: AircraftRequests) -> dict:
    """5 Hz — autopilot mode flags and reference values."""
    return {
        "type":                  "AUTOPILOT",
        "ap_master":             _i(aq, "AUTOPILOT_MASTER"),
        "wing_leveler":          _i(aq, "AUTOPILOT_WING_LEVELER"),
        "nav1_lock":             _i(aq, "AUTOPILOT_NAV1_LOCK"),
        "heading_lock":          _i(aq, "AUTOPILOT_HEADING_LOCK"),
        "heading_lock_dir_deg":  _f(aq, "AUTOPILOT_HEADING_LOCK_DIR"),
        "altitude_lock":         _i(aq, "AUTOPILOT_ALTITUDE_LOCK"),
        "altitude_lock_var_ft":  _f(aq, "AUTOPILOT_ALTITUDE_LOCK_VAR"),
        "vs_hold":               _i(aq, "AUTOPILOT_VERTICAL_HOLD"),
        "vs_hold_var_fpm":       _f(aq, "AUTOPILOT_VERTICAL_HOLD_VAR"),
        "airspeed_hold":         _i(aq, "AUTOPILOT_AIRSPEED_HOLD"),
        "airspeed_hold_var_kt":  _f(aq, "AUTOPILOT_AIRSPEED_HOLD_VAR"),
        "mach_hold":             _i(aq, "AUTOPILOT_MACH_HOLD"),
        "mach_hold_var":         _f(aq, "AUTOPILOT_MACH_HOLD_VAR"),
        "approach_hold":         _i(aq, "AUTOPILOT_APPROACH_HOLD"),
        "glideslope_hold":       _i(aq, "AUTOPILOT_GLIDESLOPE_HOLD"),
        "backcourse_hold":       _i(aq, "AUTOPILOT_BACKCOURSE_HOLD"),
        "autothrottle_active":   _i(aq, "AUTOTHROTTLE_ACTIVE"),
        "fd_active":             _i(aq, "AUTOPILOT_FLIGHT_DIRECTOR_ACTIVE"),
        "fd_pitch_rad":          _f(aq, "AUTOPILOT_FLIGHT_DIRECTOR_PITCH"),
        "fd_bank_rad":           _f(aq, "AUTOPILOT_FLIGHT_DIRECTOR_BANK"),
        "flight_level_change":   _i(aq, "AUTOPILOT_FLIGHT_LEVEL_CHANGE"),
        "attitude_hold":         _i(aq, "AUTOPILOT_ATTITUDE_HOLD"),
    }


def build_electrical(aq: AircraftRequests) -> dict:
    """2 Hz — electrical bus voltages, current draw, switch states."""
    return {
        "type":                "ELECTRICAL",
        "master_battery":      _i(aq, "ELECTRICAL_MASTER_BATTERY"),
        "avionics_master":     _i(aq, "AVIONICS_MASTER_SWITCH"),
        "main_bus_voltage":    _f(aq, "ELECTRICAL_MAIN_BUS_VOLTAGE"),
        "battery_voltage":     _f(aq, "ELECTRICAL_BATTERY_VOLTAGE"),
        "battery_load_a":      _f(aq, "ELECTRICAL_BATTERY_LOAD"),
        "total_load_a":        _f(aq, "ELECTRICAL_TOTAL_LOAD_AMPS"),
        "avionics_bus_voltage":_f(aq, "ELECTRICAL_AVIONICS_BUS_VOLTAGE"),
        "main_bus_amps":       _f(aq, "ELECTRICAL_MAIN_BUS_AMPS"),
    }


def build_environment(aq: AircraftRequests) -> dict:
    """1 Hz — ambient weather, wind, pressure, precipitation."""
    return {
        "type":                   "ENVIRONMENT",
        "ambient_temp_c":         _f(aq, "AMBIENT_TEMPERATURE"),
        "ambient_pressure_inhg":  _f(aq, "AMBIENT_PRESSURE"),
        "wind_velocity_kt":       _f(aq, "AMBIENT_WIND_VELOCITY"),
        "wind_direction_deg":     _f(aq, "AMBIENT_WIND_DIRECTION"),
        "wind_x_mps":             _f(aq, "AMBIENT_WIND_X"),
        "wind_y_mps":             _f(aq, "AMBIENT_WIND_Y"),
        "wind_z_mps":             _f(aq, "AMBIENT_WIND_Z"),
        "barometer_mb":           _f(aq, "BAROMETER_PRESSURE"),
        "sea_level_pressure_mb":  _f(aq, "SEA_LEVEL_PRESSURE"),
        "total_air_temp_c":       _f(aq, "TOTAL_AIR_TEMPERATURE"),
        "in_cloud":               _i(aq, "AMBIENT_IN_CLOUD"),
        "visibility_m":           _f(aq, "AMBIENT_VISIBILITY"),
        "precip_state":           _i(aq, "AMBIENT_PRECIP_STATE"),
    }


def build_lights(aq: AircraftRequests) -> dict:
    """1 Hz — individual light switch states plus the combined bitmask."""
    return {
        "type":              "LIGHTS",
        "light_states_mask": _i(aq, "LIGHT_STATES"),
        "strobe":            _i(aq, "LIGHT_STROBE"),
        "landing":           _i(aq, "LIGHT_LANDING"),
        "taxi":              _i(aq, "LIGHT_TAXI"),
        "beacon":            _i(aq, "LIGHT_BEACON"),
        "nav":               _i(aq, "LIGHT_NAV"),
        "panel":             _i(aq, "LIGHT_PANEL"),
        "logo":              _i(aq, "LIGHT_LOGO"),
        "wing":              _i(aq, "LIGHT_WING"),
        "cabin":             _i(aq, "LIGHT_CABIN"),
    }


def build_static(aq: AircraftRequests) -> dict:
    """0.1 Hz — aircraft identity and design parameters (rarely changes)."""
    return {
        "type":                     "STATIC",
        "title":                    _s(aq, "TITLE"),
        "atc_type":                 _s(aq, "ATC_TYPE"),
        "atc_model":                _s(aq, "ATC_MODEL"),
        "atc_id":                   _s(aq, "ATC_ID"),
        "atc_airline":              _s(aq, "ATC_AIRLINE"),
        "atc_flight_number":        _s(aq, "ATC_FLIGHT_NUMBER"),
        "num_engines":              _i(aq, "NUMBER_OF_ENGINES"),
        "engine_type":              _i(aq, "ENGINE_TYPE"),
        "is_gear_retractable":      _i(aq, "IS_GEAR_RETRACTABLE"),
        "is_tail_dragger":          _i(aq, "IS_TAIL_DRAGGER"),
        "wing_span_ft":             _f(aq, "WING_SPAN"),
        "wing_area_sqft":           _f(aq, "WING_AREA"),
        "empty_weight_lb":          _f(aq, "EMPTY_WEIGHT"),
        "max_gross_weight_lb":      _f(aq, "MAX_GROSS_WEIGHT"),
        "fuel_total_capacity_gal":  _f(aq, "FUEL_TOTAL_CAPACITY"),
        "design_speed_vs0_fps":     _f(aq, "DESIGN_SPEED_VS0"),
        "design_speed_vs1_fps":     _f(aq, "DESIGN_SPEED_VS1"),
        "design_speed_vc_fps":      _f(aq, "DESIGN_SPEED_VC"),
        "typical_descent_rate_fpm": _f(aq, "TYPICAL_DESCENT_RATE"),
    }


# ---------------------------------------------------------------------------
# UDP send
# ---------------------------------------------------------------------------

def _send(sock: socket.socket, addr: tuple, packet: dict) -> None:
    data = json.dumps(packet, separators=(",", ":")).encode("utf-8")
    try:
        sock.sendto(data, addr)
    except OSError as exc:
        log.warning("UDP send error: %s", exc)


# ---------------------------------------------------------------------------
# Main broadcast loop
# ---------------------------------------------------------------------------

def run(ip: str, port: int, retry_interval: float = 5.0) -> None:
    sm = None
    while sm is None:
        log.info("Connecting to SimConnect ...")
        try:
            sm = SimConnect()
        except ConnectionError:
            log.warning(
                "Flight Simulator not found. Retrying in %.0f s ... (Ctrl+C to quit)",
                retry_interval,
            )
            try:
                time.sleep(retry_interval)
            except KeyboardInterrupt:
                log.info("Cancelled.")
                return

    aq = AircraftRequests(sm, _time=SIM_CACHE_MS)
    eng_reqs = _build_engine_requests(sm)
    log.info("SimConnect ready.")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    addr = (ip, port)
    log.info("Broadcasting to %s:%d", ip, port)
    for ptype, interval in INTERVALS.items():
        log.info("  %-12s  %4.1f Hz", ptype, 1.0 / interval)

    builders = {
        "DYNAMICS":    lambda: build_dynamics(aq),
        "GEAR":        lambda: build_gear(aq),
        "SURFACES":    lambda: build_surfaces(aq),
        "ENGINES":     lambda: build_engines(aq, eng_reqs),
        "POSITION":    lambda: build_position(aq),
        "AUTOPILOT":   lambda: build_autopilot(aq),
        "ELECTRICAL":  lambda: build_electrical(aq),
        "ENVIRONMENT": lambda: build_environment(aq),
        "LIGHTS":      lambda: build_lights(aq),
        "STATIC":      lambda: build_static(aq),
    }

    last_sent  = {ptype: 0.0 for ptype in INTERVALS}
    tick       = min(INTERVALS.values())   # main loop sleep target = 1/20 s
    total_sent = 0

    log.info("Running. Press Ctrl+C to stop.")
    try:
        while True:
            loop_start = time.monotonic()

            for ptype, interval in INTERVALS.items():
                if loop_start - last_sent[ptype] >= interval:
                    _send(sock, addr, builders[ptype]())
                    last_sent[ptype] = loop_start
                    total_sent += 1

            sleep_for = tick - (time.monotonic() - loop_start)
            if sleep_for > 0:
                time.sleep(sleep_for)

    except KeyboardInterrupt:
        log.info("Stopped. Total packets sent: %d", total_sent)
    finally:
        sock.close()
        sm.exit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Broadcast SimConnect flight state as typed UDP JSON packets.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--ip", default=DEFAULT_IP,
        help="Destination IP.  255.255.255.255 = subnet broadcast.",
    )
    parser.add_argument(
        "--port", default=DEFAULT_PORT, type=int,
        help="UDP destination port.",
    )
    parser.add_argument(
        "--retry", default=5.0, type=float, metavar="SECS",
        help="Seconds between connection retries when the sim is not running.",
    )
    args = parser.parse_args()
    run(args.ip, args.port, args.retry)


if __name__ == "__main__":
    main()
