# probe.py — ask both HailBoard serial ports what they are
import serial, time   # pip install pyserial  (if missing)

PORTS = ["/dev/cu.usbmodem2101", "/dev/cu.usbmodem2103"]
PROBES = [b"\r\n", b"help\r\n", b"?\r\n", b"version\r\n", b"info\r\n"]

for p in PORTS:
    print(f"\n===== {p} =====")
    try:
        s = serial.Serial(p, 115200, timeout=0.5)
        time.sleep(0.3)
        banner = s.read(4096)
        if banner: print("[on open]", banner)
        for cmd in PROBES:
            s.write(cmd); time.sleep(0.4)
            resp = s.read(4096)
            print(f"{cmd!r:16} -> {resp!r}")
        s.close()
    except Exception as e:
        print("error:", e)