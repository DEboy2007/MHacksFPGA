"""Multi-outcome LMSR in integers: a software prototype (stretch goal).

Not part of the FPGA / C++ / Python comparison and not covered by docs/spec.md.
It shows that the binary design extends to N outcomes with the same kind of
arithmetic: one lookup table, adds, shifts, and one small multiply.

The market has N outcomes; exactly one will happen and its shares pay $1.
State: q[i] = net shares of outcome i sold. Cost function:

    C(q) = b * ln( sum_i exp(q[i] / b) )

The log-sum-exp is built up two terms at a time, because

    ln(e^x + e^y) = max(x, y) + ln(1 + e^-|x - y|)

and the last term is the same "tail" the binary market maker tabulates. After
the first step the running value no longer lands exactly on a table entry, so
the tail is read by linear interpolation between two neighbouring entries
(that is the one multiply: a 12-bit fraction times the gap between entries).

Fixed point: V = 2^20 * ln(sum exp(q/b)). One table step is 1/256, i.e. 4096 in V.
"""
import pathlib

F = 20                       # fractional bits of V
STEP_BITS = 12               # 2^20 / 256 = 2^12 per table step
ENTRIES = 4096               # table covers differences below 16

TABLE_PATH = pathlib.Path(__file__).resolve().parent.parent / "tables" / "softplus_tail_multi.hex"

def load_table(path=TABLE_PATH):
    return [int(line, 16) for line in path.read_text().split()]

class MultiLmsrMM:
    def __init__(self, n, table=None, lb=8, ls=3, hs=0):
        assert n >= 2 and 6 <= lb <= 8 and 0 <= ls <= lb
        self.G = table if table is not None else load_table()
        self.lb, self.ls, self.hs = lb, ls, hs    # b = 2^lb, quote size 2^ls
        self.q = [0] * n
        self.fills = 0

    def tail(self, diff):
        """2^20 * ln(1 + exp(-diff / 2^20)), for diff >= 0."""
        j, frac = diff >> STEP_BITS, diff & ((1 << STEP_BITS) - 1)
        if j >= ENTRIES - 1:
            return 0                               # below 2^20 * 1.2e-7: nothing left
        g0, g1 = self.G[j], self.G[j + 1]          # the table is decreasing
        return g0 - (((g0 - g1) * frac) >> STEP_BITS)

    def V(self, q):
        """2^20 * ln(sum_i exp(q[i] / b)), folding in one outcome at a time."""
        scale = (8 - self.lb) + STEP_BITS          # shares -> V units
        acc = q[0] << scale
        for qi in q[1:]:
            a = qi << scale
            hi, lo = (acc, a) if acc >= a else (a, acc)
            acc = hi + self.tail(hi - lo)
        return acc

    def quote(self, i):
        """(bid, ask) in cents for outcome i; 0 means that side is pulled."""
        s, up = 1 << self.ls, self.lb - self.ls
        bumped = list(self.q)
        v0 = self.V(self.q)
        bumped[i] = self.q[i] + s
        vp = self.V(bumped)
        bumped[i] = self.q[i] - s
        vm = self.V(bumped)
        ask_fp = ((vp - v0) * 100) << up           # cents, 20 fractional bits
        bid_fp = ((v0 - vm) * 100) << up
        ask = ((ask_fp + (1 << F) - 1) >> F) + self.hs      # ceil, then widen
        bid = (bid_fp >> F) - self.hs                        # floor, then widen
        # Interpolation makes this less exact than the binary version (up to
        # about 0.06 cents at quote size 1), so very rarely the two sides
        # round to the same cent. Keep at least one cent between them.
        if bid >= ask:
            bid = ask - 1
        return (bid if 1 <= bid <= 99 else 0, ask if 1 <= ask <= 99 else 0)

    def fill(self, i, buy, qty):
        """A trader bought (or sold) qty shares of outcome i at our quote."""
        bid, ask = self.quote(i)
        if not (ask if buy else bid) or not 1 <= qty <= (1 << self.ls):
            return False
        self.q[i] += qty if buy else -qty
        self.fills += 1
        return True
