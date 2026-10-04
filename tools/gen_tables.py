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

# Second table, for the multi-outcome prototype (golden/lmsr_multi.py): the
# same function without the factor of 100, with more fractional bits and twice
# the range, because there it is looked up at in-between points.
MULTI_F = 20
MULTI_ENTRIES = 4096    # covers |x| < 16

def main() -> None:
    tables = pathlib.Path(__file__).resolve().parent.parent / "tables"
    table = [tail(j) for j in range(ENTRIES)]
    assert max(table) < (1 << WIDTH)
    out = tables / "softplus_tail.hex"
    out.write_text("".join(f"{v:06x}\n" for v in table))
    print(f"wrote {out} ({ENTRIES} x {WIDTH} bits, max {max(table)}, min {min(table)})")

    multi = [round((1 << MULTI_F) * math.log1p(math.exp(-j / N))) for j in range(MULTI_ENTRIES)]
    out = tables / "softplus_tail_multi.hex"
    out.write_text("".join(f"{v:05x}\n" for v in multi))
    print(f"wrote {out} ({MULTI_ENTRIES} x {MULTI_F} bits, max {max(multi)}, min {min(multi)})")

if __name__ == "__main__":
    main()
