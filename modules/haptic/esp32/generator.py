# engine_start.py — MicroPython script for ESP32
#
# Listens for MSFS ENGINES, GEAR, and DYNAMICS UDP packets from
# sim_broadcaster and plays haptic sequences on:
#   • engine state transitions (off→on and on→off)
#   • touchdown, with intensity scaled to landing severity
#
# Hardware:
#   ESP32 GPIO14 → MOSFET SIG
#   External PSU  → Bass shaker → MOSFET drain
#   Common GND across ESP32, MOSFET, and PSU

import socket
import json
import network
from machine import Pin, PWM
import utime
import urandom

# --- Config -----------------------------------------------------------
PIN                 = 14
UDP_PORT            = 49000
LANDING_COOLDOWN_MS = 5000   # suppress re-triggers within 5 s of a landing
# ----------------------------------------------------------------------

from secrets import SSID, PASSWORD

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(SSID, PASSWORD)
print("Connecting to WiFi...")
while not wlan.isconnected():
    utime.sleep_ms(200)
print("WiFi connected:", wlan.ifconfig()[0])

pwm = PWM(Pin(PIN), freq=30, duty=0)

def set_pwm(freq, duty):
    pwm.freq(max(1, freq))
    pwm.duty(max(0, min(1023, duty)))

def jitter(amount):
    """Return a random offset in [-amount, +amount]."""
    return (urandom.getrandbits(10) % (amount * 2 + 1)) - amount

def ramp(f0, f1, d0, d1, ms, steps=30, duty_jitter=0):
    """Linearly interpolate freq and duty over `ms` milliseconds."""
    for i in range(steps + 1):
        t = i / steps
        freq = int(f0 + (f1 - f0) * t)
        duty = int(d0 + (d1 - d0) * t) + jitter(duty_jitter)
        set_pwm(freq, duty)
        utime.sleep_ms(ms // steps)

def roll(base_freq, base_duty, duration_ms, duty_jitter=80, freq_jitter=2):
    """Sustained rumble with per-step random variation."""
    end = utime.ticks_add(utime.ticks_ms(), duration_ms)
    while utime.ticks_diff(end, utime.ticks_ms()) > 0:
        f = base_freq + jitter(freq_jitter)
        d = base_duty + jitter(duty_jitter)
        set_pwm(f, d)
        utime.sleep_ms(20 + (urandom.getrandbits(5) % 25))

def thud(freq, duty, hold_ms, tail_ms):
    """Single impact: hard attack, short hold, fast decay to silence."""
    set_pwm(freq, duty)
    utime.sleep_ms(hold_ms)
    ramp(freq, freq - 2, duty, 0, tail_ms, steps=10)
    utime.sleep_ms(10)


def engine_start():
    """Prop engine start: starter crank → first fire → catches → RPM build → idle."""
    # Starter motor cranking — low, uneven rumble
    roll(18, 320, 280, duty_jitter=130, freq_jitter=3)
    # First cylinder fires
    thud(26, 950, 90, 110)
    utime.sleep_ms(70)
    # Two more catches as it sputters to life
    thud(25, 720, 55, 75)
    utime.sleep_ms(90)
    thud(27, 580, 40, 60)
    utime.sleep_ms(50)
    # RPM climbing toward idle — ramp up freq and intensity
    ramp(22, 30, 480, 760, 500, steps=25, duty_jitter=60)
    # Settled idle
    roll(30, 730, 350, duty_jitter=50, freq_jitter=1)
    # Gentle tail to mark the end of the event
    ramp(30, 28, 730, 500, 250, duty_jitter=25)
    pwm.duty(0)


def engine_stop():
    """Prop engine stop: power cut → RPM decay → silence."""
    # Abrupt power loss thud
    thud(29, 820, 65, 85)
    utime.sleep_ms(40)
    # RPM falling — frequency and intensity wind down together
    ramp(28, 18, 660, 220, 650, steps=25, duty_jitter=50)
    # Final slow prop spin fading to nothing
    ramp(18, 10, 220, 0, 450, duty_jitter=20)
    pwm.duty(0)


def landing_haptic(peak_g, touchdown_vs_fpm):
    """
    Touchdown feedback scaled to landing severity.

    Intensity is the max of two independent estimates so that a landing
    that is hard by either metric feels appropriately intense:
      G-load above 1 G : 1.0 G  → 0.0,  2.5 G  → 1.0
      Sink rate         : 50 fpm → 0.0, 800 fpm → 1.0

    intensity < 0.2  : greaser — single soft thud, brief wheel-roll whisper
    0.2 – 0.55       : firm/normal — double thud, moderate runway rumble
    > 0.55           : hard — triple thud, heavy sustained vibration
    """
    g_intensity  = max(0.0, (peak_g - 1.0) / 1.5)
    vs_intensity = max(0.0, (abs(touchdown_vs_fpm) - 50) / 750)
    intensity    = min(1.0, max(g_intensity, vs_intensity))

    impact_duty = int(350 + intensity * 650)
    impact_hold = int(35  + intensity * 85)
    impact_tail = int(55  + intensity * 65)
    roll_duty   = int(180 + intensity * 520)
    roll_dur    = int(120 + intensity * 430)

    print("Landing: G {:.2f}  VS {:.0f} fpm  intensity {:.2f}".format(
        peak_g, touchdown_vs_fpm, intensity))

    # Primary impact
    thud(30, impact_duty, impact_hold, impact_tail)

    # Secondary echo for firm landings
    if intensity > 0.35:
        utime.sleep_ms(15)
        thud(28, int(impact_duty * 0.55), int(impact_hold * 0.55), int(impact_tail * 0.7))

    # Tertiary echo for hard landings
    if intensity > 0.65:
        utime.sleep_ms(20)
        thud(27, int(impact_duty * 0.30), int(impact_hold * 0.40), int(impact_tail * 0.8))

    utime.sleep_ms(15)

    # Runway roll-out
    roll(30, roll_duty, roll_dur,
         duty_jitter=int(25 + intensity * 60), freq_jitter=2)
    ramp(30, 27, roll_duty, 0,
         int(180 + intensity * 170), duty_jitter=int(15 + intensity * 25))
    pwm.duty(0)


sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("", UDP_PORT))
sock.settimeout(0.1)

prev_starter    = None
prev_combustion = None
prev_on_ground  = None
last_g_force    = 1.0
last_vs_fpm     = 0.0
last_landing_ms = 0     # 0 = no landing yet; first touchdown always fires
baseline_set    = False
print("Listening on UDP port", UDP_PORT)

pkt_count = 0
while True:
    try:
        data, _ = sock.recvfrom(2048)
        pkt_count += 1
        if pkt_count % 10 == 0:
            print(f"[{pkt_count} packets]")
    except OSError:
        continue

    try:
        packet = json.loads(data)
    except Exception:
        continue

    ptype = packet.get("type")
    print(f"Packet: {ptype}")

    if ptype == "DYNAMICS":
        last_g_force = float(packet.get("g_force",            1.0))
        last_vs_fpm  = float(packet.get("vertical_speed_fpm", 0.0))

    elif ptype == "GEAR":
        try:
            on_ground = bool(packet.get("on_ground", 0))
            print(f"GEAR packet received: on_ground={on_ground}, prev_on_ground={prev_on_ground}")
            if prev_on_ground is None:
                # First packet: establish baseline without triggering
                prev_on_ground = on_ground
                print(f"  → GEAR baseline set to {on_ground}")
            elif not prev_on_ground and on_ground:
                # Normal landing: transitioned from air to ground
                print(f"Landing detected! G={last_g_force:.2f}  VS={last_vs_fpm:.0f} fpm")
                now = utime.ticks_ms()
                if last_landing_ms == 0 or utime.ticks_diff(now, last_landing_ms) > LANDING_COOLDOWN_MS:
                    print("  → Triggering landing haptic")
                    landing_haptic(last_g_force, last_vs_fpm)
                    last_landing_ms = utime.ticks_ms()
                else:
                    print(f"  → Suppressed (cooldown active)")
            elif on_ground and not baseline_set and (last_g_force > 1.2 or abs(last_vs_fpm) > 150):
                # First contact with ground showing impact signature (baseline still being established)
                print(f"Impact on ground during baseline! G={last_g_force:.2f}  VS={last_vs_fpm:.0f} fpm")
                now = utime.ticks_ms()
                if last_landing_ms == 0 or utime.ticks_diff(now, last_landing_ms) > LANDING_COOLDOWN_MS:
                    print("  → Triggering landing haptic")
                    landing_haptic(last_g_force, last_vs_fpm)
                    last_landing_ms = utime.ticks_ms()
                baseline_set = True
            elif on_ground:
                baseline_set = True
            prev_on_ground = on_ground
        except Exception as e:
            print(f"GEAR handler error: {e}")

    elif ptype == "ENGINES":
        starter    = bool(packet.get("starter_1",    0))
        combustion = bool(packet.get("combustion_1", 0))

        if prev_starter is None:
            # First packet: establish baseline without triggering
            prev_starter    = starter
            prev_combustion = combustion
            continue

        if starter and not prev_starter and not prev_combustion:
            print("Starter engaged — haptic")
            engine_start()

        elif not combustion and prev_combustion:
            print("Engine stopped — haptic")
            engine_stop()

        prev_starter    = starter
        prev_combustion = combustion
