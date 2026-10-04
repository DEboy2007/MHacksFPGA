"""Checks the golden model against floating point and its invariants."""
import math
from lmsr_mm import LmsrMM, load_table, KMAX, LOG2N, F, CMD_BUY, CMD_SELL

def softplus(x):
    return max(x, 0.0) + math.log1p(math.exp(-abs(x)))

G = load_table()
worst_err = 0.0
unfav = 0
checked = 0
for lb in (6, 7, 8):
    b = 1 << lb
    for ls in range(0, lb + 1):
        s = 1 << ls
        mm = LmsrMM(G, lb, ls, 0)
        dmax = KMAX >> (LOG2N - lb)
        for d in range(-dmax, dmax + 1):
            mm.d = d
            bid_px, ask_px = mm.quote()
            ask_f = 100 * b * (softplus((d + s) / b) - softplus(d / b)) / s
            bid_f = 100 * b * (softplus(d / b) - softplus((d - s) / b)) / s
            kshift, up = LOG2N - lb, lb - ls
            if (d + s) << kshift <= KMAX:
                fp = (mm.H((d + s) << kshift) - mm.H(d << kshift)) << up
                worst_err = max(worst_err, abs(fp / 2**F - ask_f))
            assert 0 <= ask_px <= 99 and 0 <= bid_px <= 99
            unfav += bool(ask_px) and ask_px < ask_f
            unfav += bool(bid_px) and bid_px > bid_f
            if ask_px and bid_px:
                assert bid_px < ask_px, (lb, ls, d, bid_px, ask_px)
            checked += 1

# H must be strictly increasing so every price difference is positive.
mm = LmsrMM(G)
assert all(mm.H(k) < mm.H(k + 1) for k in range(-KMAX, KMAX))

# Worked example from docs/lmsr.pdf section 8, rescaled to b = 64:
# buying 32 YES from d = 0 averages 100*64*(softplus(.5)-ln 2)/32 = 56.2 cents.
mm = LmsrMM(G, 6, 5, 0)
print("b=64, s=32 at d=0 (bid, ask):", mm.quote(), "(pdf example: avg 56.2c to buy)")

# d can never leave the table: walk it up and down as far as fills allow.
for lb in (6, 7, 8):
    for cmd in (CMD_BUY, CMD_SELL):
        mm = LmsrMM(G, lb, 3, 0)
        while mm.handle(cmd, 8)[0] & 1:
            pass
        print(f"b={1<<lb:3d} {'buy ' if cmd == CMD_BUY else 'sell'} run stops at d={mm.d:5d} "
              f"after {mm.fills} fills, (bid, ask)={mm.quote()}")

print(f"{checked} states checked")
print(f"max |fixed - float| before rounding to cents: {worst_err:.6f} cents")
print(f"quotes rounded against the house (vs float): {unfav}")
