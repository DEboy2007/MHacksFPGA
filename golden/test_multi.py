"""Checks the multi-outcome prototype against floating point."""
import math
import random
from lmsr_multi import MultiLmsrMM, load_table, F
from lmsr_mm import LmsrMM

def lse(xs):
    m = max(xs)
    return m + math.log(sum(math.exp(x - m) for x in xs))

G = load_table()
rng = random.Random(8)
for n in (2, 3, 4, 8):
    worst = 0.0
    unfav = 0
    quotes = 0
    for _ in range(20000):
        lb = rng.choice((6, 7, 8)); b = 1 << lb
        ls = rng.randint(0, lb); s = 1 << ls
        mm = MultiLmsrMM(n, G, lb, ls)
        mm.q = [rng.randint(-3 * b, 3 * b) for _ in range(n)]
        i = rng.randrange(n)
        x = [q / b for q in mm.q]
        xp = list(x); xp[i] += s / b
        xm = list(x); xm[i] -= s / b
        ask_f = 100 * b * (lse(xp) - lse(x)) / s
        bid_f = 100 * b * (lse(x) - lse(xm)) / s
        bumped = list(mm.q); bumped[i] += s
        ask_fp = ((mm.V(bumped) - mm.V(mm.q)) * 100) << (lb - ls)
        worst = max(worst, abs(ask_fp / 2**F - ask_f))
        bid, ask = mm.quote(i)
        if bid and ask:
            assert bid < ask
        quotes += bool(ask) + bool(bid)
        unfav += bool(ask) and ask < ask_f
        unfav += bool(bid) and bid > bid_f
    print(f"N={n}: max |fixed - float| before rounding {worst:.4f} cents; "
          f"{unfav} of {quotes} quotes rounded against the house")

# With two outcomes this is the binary market: compare with the binary model.
same = total = 0
binary = LmsrMM()
multi = MultiLmsrMM(2, G)
for d in range(-1100, 1101):
    binary.d = d
    multi.q = [d, 0]
    total += 1
    same += binary.quote() == multi.quote(0)
print(f"N=2 vs the binary market maker (b=256, size 8): {same} of {total} quotes identical")

# A three-way race: push outcome 0 up and watch the three prices.
mm = MultiLmsrMM(3, G)
print("3 outcomes, nobody has traded:", [mm.quote(i) for i in range(3)])
for _ in range(40):
    mm.fill(0, True, 8)
print("after 320 shares of outcome 0 are bought:", [mm.quote(i) for i in range(3)])
