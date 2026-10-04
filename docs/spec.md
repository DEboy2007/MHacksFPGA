# Fixed-point spec: binary LMSR market maker

The FPGA, C++ and Python market makers must
all follow this document exactly and produce identical integers.
Golden model: `golden/lmsr_mm.py`. Table generator: `tools/gen_tables.py`.
Test vectors: `golden/gen_vectors.py`. Math background: `docs/lmsr.pdf` (section 9).

## 1. Quantities

| Name | Meaning | Range | Width |
|---|---|---|---|
| `d` | net YES shares sold (`q_Y - q_N`) | bounded by the quoting rules, see §5 | signed 16 |
| `LB` | `b = 2^LB`, liquidity preset | 6, 7, 8 (b = 64, 128, 256) | 2 bits |
| `LS` | `s = 2^LS`, quote size in shares | 0 .. `LB` | 4 bits |
| `hs` | extra half-spread, whole cents | 0 .. 15 | 4 bits |
| `kill` | kill switch | 0 / 1 | 1 bit |
| prices | whole cents | 1 .. 99 | 7 bits |
| `fills` | accepted fills since reset | wraps | unsigned 32 |

Money is in cents; a YES contract pays 100. Internal prices carry `F = 16`
fractional bits, so `ONE = 65536` means one cent.

## 2. The one lookup table

`softplus(x) = max(x, 0) + ln(1 + e^-|x|)`. Only the second term (the "tail")
needs a table: it is symmetric, bounded by `ln 2`, and decays to zero.

```
G[j] = round(100 * 2^16 * ln(1 + exp(-j / 256)))     j = 0 .. 2047
```

- 2048 entries x 24 bits = 12 of the 32 BRAMs. Largest entry 4,542,609.
- The table is indexed by `k = d * 256 / b`, i.e. `x = d/b = k/256`. Because
  `b` is 64, 128 or 256, `k = d << (8 - LB)` is exact and **one table serves
  every `b` preset**.
- File: `tables/softplus_tail.hex`, one 6-digit hex word per line. Verilog loads
  it with `$readmemh`; C++ and Python parse the same file.

Scaled softplus for a signed index, valid for `-2047 <= k <= 2047`:

```
H(k) = G[k] + k * 25600      if k >= 0        (25600 = 100 << (16 - 8))
H(k) = G[-k]                 if k <  0
```

`H(k) = 100 * 2^16 * softplus(k/256)` up to table rounding. It is strictly
increasing (checked by `golden/test_ref.py`), so every difference below is positive
and all arithmetic is unsigned. `k * 25600` is `(k<<14) + (k<<13) + (k<<10)`:
no multiplier needed.

## 3. Quotes

```
k0 = d       << (8 - LB)
kp = (d + s) << (8 - LB)
km = (d - s) << (8 - LB)

ask_fp = (H(kp) - H(k0)) << (LB - LS)      # = b * [C(d+s) - C(d)] / s, exact shift
bid_fp = (H(k0) - H(km)) << (LB - LS)

ask = ((ask_fp + 65535) >> 16) + hs        # ceil to cents, then widen
bid =  (bid_fp >> 16)          - hs        # floor to cents, then widen
```

`LS <= LB` is required so that `b/s` is a left shift and nothing is lost.

A side is **pulled** (price 0, size 0) when any of these hold; otherwise its
size is `s`:

1. `kill` is set (both sides).
2. A lookup would leave the table. The ask needs `k0 >= -2047` and
   `kp <= 2047`; the bid needs `km >= -2047` and `k0 <= 2047`.
3. Its price is outside 1..99: ask if `ask > 99`, bid if `bid < 1`.
   (Also ask if `ask < 1` / bid if `bid > 99`; these cannot occur for `hs >= 0`
   but implementations test the full range.)

**Deviation from the original brief:** the brief says "clamped to 1..99". This
spec pulls the quote instead. Clamping an ask of 100 down to 99, or a bid of 0
up to 1, moves the price *against* the house; pulling does not.

The outputs are two integers, `bid_px` and `ask_px`; 0 means pulled. The size
of a live side is always `s`.

## 4. Requests and state changes

The MM handles one request at a time and answers each with a fresh quote.

| cmd | Name | `arg` (16 bits) | Accepted when | Effect |
|---|---|---|---|---|
| 1 | BUY | `qty` | ask is live and `1 <= qty <= s` | `d += qty`, `fills += 1` |
| 2 | SELL | `qty` | bid is live and `1 <= qty <= s` | `d -= qty`, `fills += 1` |
| 3 | CONFIG | see below | `LB <= 8`, `LS <= LB`, bits 15..11 zero | sets `LB, LS, hs, kill` |
| 4 | RESET | ignored | always | `d = 0`, `fills = 0`, reference back to 50; config kept |
| 5 | QUERY | ignored | always | none |
| 6 | REFERENCE | `price_cents` | `0 <= price_cents <= 100` | shifts both live quotes around the external YES reference; does not change `d` |

BUY means a trader bought YES at our ask; SELL means a trader sold YES to us at
our bid. A request that is not accepted changes nothing but is still answered.

CONFIG `arg`: bits 1..0 = `LB - 6`, bits 5..2 = `LS`, bits 9..6 = `hs`,
bit 10 = `kill`. Power-on config: `LB = 8, LS = 3, hs = 0, kill = 0`.

CONFIG never touches `d`. If `b` is reduced while holding a large position,
`k0` can fall outside the table; rule 2 then pulls both sides until `b` is
raised again or RESET is sent.

Partial fills (`qty < s`) trade at the quoted price for `s`. By convexity of
the cost function that is never worse for the house than the exact LMSR price.

Cash/P&L is tracked by the exchange simulator, not the pricing core (it needs
`price * qty`, and the FPGA has no multipliers).

REFERENCE is the deterministic external-price input used by the live
Polymarket adapter. The V1 value is the floor of the YES best-bid/best-ask
midpoint after converting both prices to cents. The core computes its normal
inventory quote (section 3, including the pull rules). Each side that is live
is then moved by `price_cents - 50`, and pulled if the result is outside
1..99. It is never clamped. The reference is 50 at power-on and after RESET,
so replayed order streams are unaffected by an earlier live session. The
external reference is separate from the inventory state `d`; public market
trades never issue BUY or SELL requests.

## 5. Reachable range

Rule 3 stops the MM selling YES once the ask would exceed 99 cents, which is
near `d/b = 4.6`, well inside the table (`|d/b| < 8`). Measured with `s = 8`:

| b | d stops at |
|---|---|
| 64 | +-296 |
| 128 | +-592 |
| 256 | +-1176 |

So `d` fits easily in 16 signed bits and rule 2 is a backstop.
Worst-case loss is `100 * b * ln 2` cents: $44, $89, $177 for the three presets.

## 6. Wire protocol

Binary, identical for all three MMs. Over UART it is 115200 8N1. Multi-byte
fields are little-endian. `xor` is the XOR of all preceding bytes of the frame.

Request, host to MM, 5 bytes:

| Byte | 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| | `0xA5` | `cmd` | `arg` low | `arg` high | `xor` |

Reply, MM to host, 13 bytes:

| Byte | 0 | 1 | 2 | 3 | 4-5 | 6-7 | 8-11 | 12 |
|---|---|---|---|---|---|---|---|---|
| | `0x5A` | `status` | `bid_px` | `ask_px` | `d` | `seq` | `latency` | `xor` |

- `status`: bit 0 = accepted, bits 3..1 = `cmd` echoed, bit 4 = `kill`,
  bits 6..5 = `LB - 6`, bit 7 = 0. All reflect the state after the request.
  (Bit 7 = 1 marks a board-button notice, FPGA only: section 10.)
- `seq`: low 16 bits of `fills`.
- `latency`: see section 8. **Excluded from the bit-exact comparison**; every
  other byte must match across implementations.
- A frame with a bad `xor` or an unknown `cmd` is dropped with no reply.
- Framing: the receiver waits for `0xA5`, then takes the next 4 bytes. If more
  than 10 ms pass between bytes of a frame it goes back to waiting for `0xA5`.
  The host sends one request, then waits for the reply (or a timeout) before
  sending the next. (The 10 ms rule is for the UART; the C++ and Python
  market makers read from a pipe, which cannot lose bytes, and skip it.)

One round trip is 18 bytes, about 1.6 ms at 115200 baud, so the link carries
roughly 600 requests per second.

## 7. Accuracy versus floating point

Measured by `golden/test_ref.py` over every `(LB, LS, d)`:

- Largest difference between `ask_fp / 2^16` and the float LMSR price:
  **0.0038 cents**.
- The final cent values are rounded in the house's favour relative to the
  float price in all but 16 of roughly 120,000 quotes; in those 16 the float
  price sits within 0.004 cents of a cent boundary.

## 8. Latency definitions

**Compute latency (primary metric)** goes in the reply's `latency` field:

- **FPGA:** clock cycles from the UART receiver delivering the request's last
  byte (it samples the middle of the stop bit) to the reply frame being ready
  to transmit. One cycle is 83.33 ns at 12 MHz. Measured on the board: always
  **8 cycles = 667 ns** (1 to check the frame, 7 in the quote core).
- **C++ / Python:** nanoseconds from the last request byte being returned by
  the read call to the reply being ready, measured before the write.
  Timer: `clock_gettime_nsec_np(CLOCK_UPTIME_RAW)`; its resolution is reported
  with the results.

Requests arrive one at a time at the rate the UART link can carry, for all
three MMs. An unthrottled CPU run, and a CPU run under background load, are
reported separately and labelled.

**Round-trip latency (secondary)** is measured by the exchange simulator from
writing the request to reading the full reply. For the FPGA this is dominated
by the serial link and is labelled as such.

Report median, p99, p99.9 and max, plus a histogram.

## 9. Test vectors

`golden/gen_vectors.py` (seed 270) writes 6,971 request/reply pairs covering
every `b` preset, balanced and trending flow that runs into the price limits,
bad sizes, kill, invalid configs, and shrinking `b` under a large position.

- `golden/vectors.txt`: readable.
- `golden/vectors.hex`: one 80-bit word per line for `$readmemh`:
  `cmd[79:72] arg[71:56] status[55:48] bid[47:40] ask[39:32] d[31:16] seq[15:0]`.

## 10. FPGA notes

- The three lookups (`km`, `k0`, `kp`) come from one BRAM on consecutive
  cycles; this sets the pipeline depth and therefore the fixed cycle count.
- Build (nextpnr, 12 MHz clock): 1,445 of 7,680 logic cells, 12 of 32 BRAMs,
  estimated maximum clock 46.9 MHz.
- The FPGA UART is `/dev/cu.usbmodem2103`. The RP2040 bridge drops bytes when
  more than about 32 are in flight in both directions at once; the
  one-request-then-one-reply rule in section 6 stays far below that.
- **Switches:** with SW17 up, the switches replace the host's config: SW1:0
  selects `LB` as 6, 7 or 8 (`b` = 64, 128, 256), SW5:2 selects `LS` (clamped
  to `LB`), and SW9:6 selects `hs` in cents. With SW17 down, host CONFIG
  messages have full control. The switches have no kill function.
- LEDY3..0 show: switches enabled (SW17), kill, core busy, byte received.
- Build (buttons, switches, decimal displays): 2,010 logic cells, estimated
  maximum clock 46.0 MHz.
- **Displays** are decimal: HEX7-6 bid, HEX5-4 ask (`--` when pulled), HEX3-0
  accepted fills, wrapping from 9999 to 0000. `make demo` streams 9,999 orders
  so the count never wraps during a demo.
- **Buttons** (FPGA only):

  | Button | Effect on the board | Notice type |
  |---|---|---|
  | KEY0 | restart while held: `d = 0`, `fills = 0`, power-on config | 0, sent on release and at power-on |
  | KEY1 | kill switch on/off (same `kill` bit CONFIG sets); LEDR0 shows it | 1 |
  | KEY3 | asks the laptop to pause the order feed; LEDG1 lights | 2 |
  | KEY2 | asks the laptop to resume; LEDG1 goes out | 3 |
  | any of SW17, SW9..0 moved | the quote is recomputed with the new settings | 4 |

- **Notices:** each button press makes the board send one reply-format frame
  unasked: status bit 7 = 1, the notice type in the `cmd` bits, bit 0 = 1, and
  the current quote, `d` and `seq`. The restart notice is `0xC1` with quote
  49/51. The latency field of a notice means nothing.
- A notice and a reply never overlap on the wire. If a request arrives while a
  notice is being sent it waits and is answered afterwards (its latency field
  then includes the wait). This only happens when a button is pressed.
- `exchange --demo` acts on notices (restart, pause, resume, kill). Outside
  demo mode any notice during a run is an error, so benchmark runs cannot be
  silently disturbed by a button.

## 11. Order-flow file

Written by `gen/gen`, replayed by `exchange/exchange` into every market maker,
so all three see the same stream. Little-endian.

Header, 32 bytes:

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | magic `LMEV` |
| 4 | 2 | version = 1 |
| 6 | 2 | CONFIG `arg` the exchange sends first (`LB`, `LS`, `hs`) |
| 8 | 4 | number of events |
| 12 | 8 | seed |
| 20 | 2 | `p0`: true probability before the news, in 1/100 cent (0..10000) |
| 22 | 2 | `p1`: true probability after the news |
| 24 | 4 | event index of the news, `0xFFFFFFFF` = none |
| 28 | 1 | outcome: 1 = YES happens (drawn from the final truth) |
| 29 | 3 | zero |

Event, 8 bytes: one trader arriving.

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | 1 = informed, 0 = noise |
| 1 | 1 | noise only: 1 = wants to buy YES, 2 = wants to sell |
| 2 | 2 | `qty` (noise: 1..s, informed: s) |
| 4 | 2 | truth at this moment, in 1/100 cent |
| 6 | 2 | zero |

What the exchange does with an event, given the current quote:

- **Noise:** trades `qty` on its side if that side is live, otherwise nothing.
- **Informed:** buys if the ask is live and `ask * 100 < truth`; otherwise
  sells if the bid is live and `bid * 100 > truth`; otherwise nothing.

The exchange starts every run with CONFIG then RESET. The recorded quote
stream begins with the RESET reply, so it does not depend on what the market
maker was doing beforehand.
