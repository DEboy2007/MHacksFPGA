#!/usr/bin/env python3
"""M3 check: replay the golden request stream into a market maker over a
serial port and compare every reply with the golden model, byte for byte
(the latency field is excluded, as docs/spec.md section 6 says).

    python3 tools/replay.py [/dev/cu.usbmodem2103] [max_vectors]
"""
import collections
import pathlib
import sys
import time
import serial

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "golden"))
from lmsr_mm import LmsrMM, encode_request, xor8, OUT_LEN, CMD_RESET, CMD_CONFIG, pack_config  # noqa: E402

CLK_NS = 1e9 / 12e6

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/cu.usbmodem2103"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    vectors = [(int(l[0:2], 16), int(l[2:6], 16))
               for l in (ROOT / "golden" / "vectors.hex").read_text().split()][:limit]
    # Put the board in the power-on state first, whatever it was doing before.
    preamble = [(CMD_CONFIG, pack_config(8, 3, 0, False)), (CMD_RESET, 0)]

    mm = LmsrMM()
    lat = collections.Counter()
    errors = 0
    with serial.Serial(port, 115200, timeout=0.5) as s:
        time.sleep(0.2)
        s.reset_input_buffer()
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
    for cycles, count in sorted(lat.items()):
        print(f"  latency {cycles} clocks = {cycles * CLK_NS:.0f} ns: {count} replies")
    print("final state:", "bid %d ask %d d %d fills %d" % (*mm.quote(), mm.d, mm.fills))
    sys.exit(1 if errors else 0)

if __name__ == "__main__":
    main()
