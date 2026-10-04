#!/usr/bin/env python3
"""M1 check: find which serial port is the FPGA UART by sending bytes and
seeing which port echoes them (needs the echo bitstream: apio upload -e echo)."""
import glob
import sys
import time
import serial

PAYLOAD = bytes([0x00, 0xFF, 0xA5, 0x5A, 0x01, 0x80]) + bytes(range(32, 96))

def try_port(port):
    try:
        with serial.Serial(port, 115200, timeout=1.0) as s:
            time.sleep(0.2)
            s.reset_input_buffer()
            s.write(PAYLOAD)
            got = s.read(len(PAYLOAD))
    except serial.SerialException as e:
        return f"error: {e}"
    if got == PAYLOAD:
        return "ECHO OK"
    return f"no echo (got {len(got)} bytes: {got[:16].hex()})"

ports = sys.argv[1:] or sorted(glob.glob("/dev/cu.usbmodem*"))
for port in ports:
    print(f"{port}: {try_port(port)}")
