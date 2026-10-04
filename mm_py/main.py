#!/usr/bin/env python3
"""mm_py - the Python market maker. Speaks the binary protocol of docs/spec.md
section 6 on stdin/stdout, one request at a time.

The pricing logic is the golden model itself (golden/lmsr_mm.py); this file
only adds the byte transport and the latency measurement: nanoseconds from the
read that returned the last request byte to the quote being ready, taken
before the reply is written.
"""
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "golden"))
from lmsr_mm import LmsrMM, load_table, decode_request, encode_reply, SYNC_IN, IN_LEN  # noqa: E402

CLOCK = time.CLOCK_UPTIME_RAW

def read_exact(n):
    buf = b""
    while len(buf) < n:
        chunk = os.read(0, n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf

def main():
    table = sys.argv[1] if len(sys.argv) > 1 else None
    mm = LmsrMM(load_table(pathlib.Path(table)) if table else None)
    while True:
        first = read_exact(1)                 # wait for the sync byte
        if first is None:
            return
        if first[0] != SYNC_IN:
            continue
        rest = read_exact(IN_LEN - 1)
        if rest is None:
            return
        t0 = time.clock_gettime_ns(CLOCK)
        req = decode_request(first + rest)
        resp = req and mm.handle(*req)
        if not resp:
            continue
        latency = time.clock_gettime_ns(CLOCK) - t0
        os.write(1, encode_reply(resp, latency))

if __name__ == "__main__":
    main()
