#!/usr/bin/env python3
"""Generate the softplus-tail lookup table shared by the FPGA, C++ and Python MMs.

G[j] = round(100 * 2^F * ln(1 + exp(-j/N)))   for j = 0 .. ENTRIES-1

Output: tables/softplus_tail.hex, one 24-bit word per line (6 hex digits),
readable by Verilog $readmemh and trivially by C++/Python. See docs/spec.md.
"""
import math
import pathlib

F = 16          # fractional bits of a cent
N = 256         # table steps per unit of x = d/b
ENTRIES = 2048  # covers |x| < 8
WIDTH = 24      # bits per entry

def tail(j: int) -> int:
    return round(100 * (1 << F) * math.log1p(math.exp(-j / N)))

def main() -> None:
    table = [tail(j) for j in range(ENTRIES)]
    assert max(table) < (1 << WIDTH)
    out = pathlib.Path(__file__).resolve().parent.parent / "tables" / "softplus_tail.hex"
    out.write_text("".join(f"{v:06x}\n" for v in table))
    print(f"wrote {out} ({ENTRIES} x {WIDTH} bits, max {max(table)}, min {min(table)})")

if __name__ == "__main__":
    main()
