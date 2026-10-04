"""Fixed-point binary LMSR market maker: the golden model for docs/spec.md.

Integer-only on purpose: every operation here has a one-line equivalent in
Verilog and C++, so all three implementations can be bit-exact.
"""
import pathlib

F = 16                    # fractional bits of a cent
LOG2N = 8                 # table steps per unit of x = d/b is 2^LOG2N
ENTRIES = 2048
KMAX = ENTRIES - 1        # largest |k| the table covers
LIN = 100 << (F - LOG2N)  # linear part of softplus, per table step
ONE = 1 << F

# Protocol (spec section 6)
SYNC_IN, SYNC_OUT = 0xA5, 0x5A
CMD_BUY, CMD_SELL, CMD_CONFIG, CMD_RESET, CMD_QUERY, CMD_REFERENCE = 1, 2, 3, 4, 5, 6
IN_LEN, OUT_LEN = 5, 13

TABLE_PATH = pathlib.Path(__file__).resolve().parent.parent / "tables" / "softplus_tail.hex"

def load_table(path=TABLE_PATH):
    return [int(line, 16) for line in path.read_text().split()]

def pack_config(lb, ls, hs, kill):
    return (lb - 6) | (ls << 2) | (hs << 6) | (int(kill) << 10)

def xor8(data):
    x = 0
    for byte in data:
        x ^= byte
    return x

def encode_request(cmd, arg):
    body = bytes([SYNC_IN, cmd, arg & 0xFF, (arg >> 8) & 0xFF])
    return body + bytes([xor8(body)])

def decode_request(frame):
    """Returns (cmd, arg), or None if the frame is malformed."""
    if len(frame) != IN_LEN or frame[0] != SYNC_IN or xor8(frame) != 0:
        return None
    return frame[1], frame[2] | (frame[3] << 8)

class LmsrMM:
    def __init__(self, table=None, lb=8, ls=3, hs=0):
        self.G = table if table is not None else load_table()
        self.lb, self.ls, self.hs = lb, ls, hs   # b = 2^lb, quote size s = 2^ls
        self.kill = False
        self.d = 0          # net YES shares sold
        self.fills = 0      # accepted fills, 32-bit wrapping
        self.reference = 50 # external YES reference price, whole cents

    def H(self, k):
        """100 * 2^F * softplus(k/N), for -KMAX <= k <= KMAX."""
        return self.G[k] + k * LIN if k >= 0 else self.G[-k]

    def quote(self):
        """Returns (bid_px, ask_px) in cents; 0 means that side is pulled."""
        s = 1 << self.ls
        up = self.lb - self.ls              # multiply by b/s
        kshift = LOG2N - self.lb            # d -> table index
        k0 = self.d << kshift
        kp = (self.d + s) << kshift
        km = (self.d - s) << kshift

        ask_px = 0
        if not self.kill and k0 >= -KMAX and kp <= KMAX:
            ask_fp = (self.H(kp) - self.H(k0)) << up
            ask = ((ask_fp + ONE - 1) >> F) + self.hs    # ceil, then widen
            if 1 <= ask <= 99:
                ask_px = ask

        bid_px = 0
        if not self.kill and km >= -KMAX and k0 <= KMAX:
            bid_fp = (self.H(k0) - self.H(km)) << up
            bid = (bid_fp >> F) - self.hs                # floor, then widen
            if 1 <= bid <= 99:
                bid_px = bid

        # REFERENCE: move each live side by (reference - 50) cents and pull
        # it if that takes it outside 1..99.
        shift = self.reference - 50
        if bid_px and not 1 <= bid_px + shift <= 99:
            bid_px = 0
        elif bid_px:
            bid_px += shift
        if ask_px and not 1 <= ask_px + shift <= 99:
            ask_px = 0
        elif ask_px:
            ask_px += shift
        return bid_px, ask_px

    def handle(self, cmd, arg):
        """Apply one request. Returns (status, bid_px, ask_px, d, seq), or None
        for an unknown command (no reply is sent)."""
        ok = False
        if cmd in (CMD_BUY, CMD_SELL):
            bid_px, ask_px = self.quote()
            live = ask_px if cmd == CMD_BUY else bid_px
            if live and 1 <= arg <= (1 << self.ls):
                self.d += arg if cmd == CMD_BUY else -arg
                self.fills = (self.fills + 1) & 0xFFFFFFFF
                ok = True
        elif cmd == CMD_CONFIG:
            lb, ls = 6 + (arg & 3), (arg >> 2) & 15
            if lb <= 8 and ls <= lb and arg < (1 << 11):
                self.lb, self.ls = lb, ls
                self.hs, self.kill = (arg >> 6) & 15, bool((arg >> 10) & 1)
                ok = True
        elif cmd == CMD_RESET:
            self.d, self.fills, self.reference, ok = 0, 0, 50, True
        elif cmd == CMD_QUERY:
            ok = True
        elif cmd == CMD_REFERENCE:
            if arg <= 100:
                self.reference = arg
                ok = True
        else:
            return None
        bid_px, ask_px = self.quote()
        status = int(ok) | (cmd << 1) | (int(self.kill) << 4) | ((self.lb - 6) << 5)
        return status, bid_px, ask_px, self.d, self.fills & 0xFFFF

    def handle_frame(self, frame, latency=0):
        """Bytes in, bytes out (or None if the frame is dropped)."""
        req = decode_request(frame)
        resp = req and self.handle(*req)
        return encode_reply(resp, latency) if resp else None

def encode_reply(resp, latency=0):
    status, bid_px, ask_px, d, seq = resp
    body = bytes([SYNC_OUT, status, bid_px, ask_px]) \
        + (d & 0xFFFF).to_bytes(2, "little") + seq.to_bytes(2, "little") \
        + min(latency, 0xFFFFFFFF).to_bytes(4, "little")
    return body + bytes([xor8(body)])
