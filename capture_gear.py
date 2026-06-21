#!/usr/bin/env python3
"""Simple UDP listener to capture and display GEAR packets from the broadcaster."""

import socket
import json
import sys

UDP_PORT = 49000
BUFFER_SIZE = 2048

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("", UDP_PORT))

print(f"Listening on UDP port {UDP_PORT}...")
print("Waiting for packets (Ctrl+C to stop)\n")

packet_counts = {}
gear_count = 0

try:
    while True:
        data, addr = sock.recvfrom(BUFFER_SIZE)
        try:
            packet = json.loads(data)
            ptype = packet.get("type", "UNKNOWN")

            # Count packet types
            packet_counts[ptype] = packet_counts.get(ptype, 0) + 1

            # Display GEAR packets
            if ptype == "GEAR":
                gear_count += 1
                on_ground = packet.get("on_ground")
                print(f"[{gear_count}] GEAR: on_ground={on_ground}")
                if "gear_handle" in packet:
                    print(f"      gear_handle={packet.get('gear_handle')}, "
                          f"gear_total_pct={packet.get('gear_total_pct'):.1f}%")
                print()
        except json.JSONDecodeError:
            print(f"Failed to parse packet: {data[:50]}")
        except Exception as e:
            print(f"Error processing packet: {e}")

except KeyboardInterrupt:
    print("\n\nStopped.")
    print("\nPacket counts:")
    for ptype, count in sorted(packet_counts.items()):
        print(f"  {ptype}: {count}")
    if gear_count == 0:
        print("\n⚠️  No GEAR packets received!")
    sys.exit(0)
