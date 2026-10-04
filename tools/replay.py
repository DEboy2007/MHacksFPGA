#!/usr/bin/env python3
"""M3 check: replay the golden request stream into a market maker over a
serial port and compare every reply with the golden model, byte for byte
(the latency field is excluded, as docs/spec.md section 6 says).

    python3 tools/replay.py                       # the FPGA on its serial port
    python3 tools/replay.py --port /dev/cu.usbmodemXXXX
    python3 tools/replay.py --cmd mm_cpp/mm_cpp   # a market maker on stdin/stdout
    python3 tools/replay.py --cmd "python3 mm_py/main.py"
"""
import argparse
import collections
import pathlib
import shlex
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "golden"))
from lmsr_mm import LmsrMM, encode_request, xor8, OUT_LEN, CMD_RESET, CMD_CONFIG, pack_config  # noqa: E402

CLK_NS = 1e9 / 12e6

class SerialLink:
    unit = "clocks"
    def __init__(self, port):
        import serial
        self.s = serial.Serial(port, 115200, timeout=0.5)
        time.sleep(0.2)
        self.s.reset_input_buffer()
    def write(self, data):
        self.s.write(data)
    def read(self, n):
        return self.s.read(n)

class ChildLink:
    unit = "ns"
    def __init__(self, cmd):
        self.p = subprocess.Popen(shlex.split(cmd), stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, bufsize=0, cwd=ROOT)
    def write(self, data):
        self.p.stdin.write(data)
    def read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.p.stdout.read(n - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/cu.usbmodem2103")
    ap.add_argument("--cmd", help="run this market maker instead of using the serial port")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    limit = args.limit
    vectors = [(int(l[0:2], 16), int(l[2:6], 16))
               for l in (ROOT / "golden" / "vectors.hex").read_text().split()][:limit]
    # Put the board in the power-on state first, whatever it was doing before.
    preamble = [(CMD_CONFIG, pack_config(8, 3, 0, False)), (CMD_RESET, 0)]

    mm = LmsrMM()
    lat = collections.Counter()
    errors = 0
    s = ChildLink(args.cmd) if args.cmd else SerialLink(args.port)
    t0 = time.time()
    for i, (cmd, arg) in enumerate(preamble + vectors):
        s.write(encode_request(cmd, arg))
        got = s.read(OUT_LEN)
        want = mm.handle_frame(encode_request(cmd, arg))
        if len(got) != OUT_LEN or xor8(got) != 0 or got[:8] != want[:8]:
            errors += 1
            print(f"MISMATCH #{i - len(preamble)} cmd={cmd} arg={arg}: got {got.hex()} want {want[:8].hex()}")
            if errors >= 10:
                break
        else:
            lat[int.from_bytes(got[8:12], "little")] += 1
    dt = time.time() - t0
    n = len(vectors)
    print(f"{n} vectors in {dt:.1f} s ({(n + 2) / dt:.0f} round trips/s), {errors} mismatches")
    if s.unit == "clocks":
        for cycles, count in sorted(lat.items()):
            print(f"  latency {cycles} clocks = {cycles * CLK_NS:.0f} ns: {count} replies")
    else:
        ns = sorted(lat.elements())
        print(f"  latency ns: median {ns[len(ns) // 2]}, p99 {ns[len(ns) * 99 // 100]}, max {ns[-1]}")
    print("final state:", "bid %d ask %d d %d fills %d" % (*mm.quote(), mm.d, mm.fills))
    sys.exit(1 if errors else 0)

if __name__ == "__main__":
    main()
