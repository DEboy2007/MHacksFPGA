#!/usr/bin/env python3
"""Generate golden test vectors: a request stream and the expected replies.

  golden/vectors.txt  human-readable, one request/reply per line
  golden/vectors.hex  one 80-bit word per line for Verilog $readmemh:
                      cmd[79:72] arg[71:56] status[55:48] bid[47:40] ask[39:32]
                      d[31:16] seq[15:0]
"""
import pathlib
import random
from lmsr_mm import (LmsrMM, pack_config, CMD_BUY, CMD_SELL, CMD_CONFIG,
                     CMD_RESET, CMD_QUERY, CMD_REFERENCE)

SEED = 270
HERE = pathlib.Path(__file__).resolve().parent

def requests(rng):
    """Phases chosen to hit every rule: trends that run into the price limits,
    bad sizes, kill, spread, every b preset, and switching b mid-position."""
    yield CMD_QUERY, 0
    for lb, ls, hs in ((8, 3, 0), (7, 4, 1), (6, 0, 0), (6, 6, 3), (8, 8, 0), (8, 5, 15)):
        yield CMD_RESET, 0
        yield CMD_CONFIG, pack_config(lb, ls, hs, False)
        s = 1 << ls
        for p_buy in (0.5, 0.9, 0.1, 0.5):          # balanced, up-trend, down-trend
            for _ in range(250):
                r = rng.random()
                if r < 0.03:
                    yield rng.choice((CMD_BUY, CMD_SELL)), rng.choice((0, s + 1, 0xFFFF))
                elif r < 0.04:
                    yield CMD_QUERY, rng.randrange(1 << 16)
                else:
                    qty = s if rng.random() < 0.5 else rng.randint(1, s)
                    yield (CMD_BUY if rng.random() < p_buy else CMD_SELL), qty
        yield CMD_CONFIG, pack_config(lb, ls, hs, True)       # kill
        yield CMD_BUY, 1
        yield CMD_SELL, 1
        yield CMD_CONFIG, pack_config(lb, ls, hs, False)
    # Invalid configs (LS > LB, LB = 9, stray high bits) must be rejected.
    for bad in (pack_config(6, 7, 0, False), 3, 1 << 11, 0xFFFF):
        yield CMD_CONFIG, bad
    # Build a big position at b=256, then shrink b so d falls outside the table.
    yield CMD_CONFIG, pack_config(8, 8, 0, False)
    for _ in range(6):
        yield CMD_BUY, 256
    for lb in (7, 6, 8):
        yield CMD_CONFIG, pack_config(lb, 3, 0, False)
        yield CMD_SELL, 8
        yield CMD_BUY, 8
    # REFERENCE: shift the quotes around an external price, including shifts
    # that push a side out of 1..99, invalid values, and RESET clearing it.
    yield CMD_RESET, 0
    for lb, ls, hs in ((8, 3, 0), (6, 2, 2), (7, 5, 0)):
        yield CMD_CONFIG, pack_config(lb, ls, hs, False)
        s = 1 << ls
        for _ in range(300):
            r = rng.random()
            if r < 0.30:
                yield CMD_REFERENCE, rng.choice((0, 1, 2, 10, 30, 49, 50, 51, 70, 90, 98, 99, 100, 101, 0xFFFF))
            else:
                yield (CMD_BUY if rng.random() < 0.5 else CMD_SELL), rng.randint(1, s)
        yield CMD_REFERENCE, 80
        yield CMD_RESET, 0                       # also puts the reference back to 50
        yield CMD_QUERY, 0
    yield CMD_CONFIG, pack_config(8, 3, 0, False)

def main():
    rng = random.Random(SEED)
    mm = LmsrMM()
    txt, hexw = [], []
    for cmd, arg in requests(rng):
        status, bid, ask, d, seq = mm.handle(cmd, arg)
        txt.append(f"{cmd} {arg:5d} -> status={status:02x} bid={bid:2d} ask={ask:2d} d={d:5d} seq={seq}")
        hexw.append(f"{cmd:02x}{arg:04x}{status:02x}{bid:02x}{ask:02x}{d & 0xFFFF:04x}{seq:04x}")
    (HERE / "vectors.txt").write_text("\n".join(txt) + "\n")
    (HERE / "vectors.hex").write_text("\n".join(hexw) + "\n")
    print(f"wrote {len(txt)} vectors")

if __name__ == "__main__":
    main()
