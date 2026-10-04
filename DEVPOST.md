# FPGA Prediction Market Maker

This project is a prediction-market market maker built on an FPGA circuit. It reprices in exactly 188 nanoseconds, every time, and matches its C++ and Python replicas bit for bit.

**Here are the main features**:
- New quote in 188 ns on every request, with zero jitter
- Same market maker in Verilog, C++ and Python, with identical prices from all three
- LMSR pricing on a chip with no multiplier, within 0.004 cents of the exact formula
- Kill switch that pulls all quotes out of the market instantly
- Buttons to pause, resume and restart the order stream; switches to change the spread
- Live mode that quotes around a real Polymarket market's price
- Simulated exchange with random and informed traders, tracking inventory and profit

## Inspiration

Trading firms put their fastest logic on FPGAs, and the reason is less about raw speed than about consistency. Software is fast on average but occasionally stalls, and a market maker is most exposed in exactly those moments, when its prices are out of date. We wanted to see that difference on a problem small enough to finish in a weekend, so we built a market maker for binary (yes/no) prediction markets like those on Polymarket.

## What it does

A market maker always posts a price it will buy at and a price it will sell at, and updates both after every trade. Ours prices a yes/no market with the Logarithmic Market Scoring Rule (LMSR), a standard rule where the more "yes" it has sold, the higher it quotes, and its worst-case loss is capped. We built the same market maker three times: as a circuit on a small FPGA board, in C++, and in Python. All three produce identical prices for the same stream of trades, so the only thing left to compare is time.

The whole state of the market maker is one number, \(d\), the net number of "yes" shares it has sold. With a liquidity setting \(b\), the fair price of a "yes" share and the cost function behind it are

$$p(d) = \frac{1}{1 + e^{-d/b}}, \qquad C(d) = b \ln\left(1 + e^{d/b}\right)$$

A trade costs the change in \(C\), so the ask and bid for an order of \(s\) shares are

$$\text{ask} = \frac{C(d+s) - C(d)}{s}, \qquad \text{bid} = \frac{C(d) - C(d-s)}{s}$$

The market maker can never lose more than \(b \ln 2\), no matter how anyone trades.

The FPGA takes 9 clock ticks, 188 ns, to produce a new quote, on every single request we measured. C++ is faster on a typical request (41 ns) but its slowest reply took 1,042 ns, about 5.5 times slower than the FPGA's. Python ranged from 2,500 ns to 14,458 ns. The board also runs a live demo. One button is a kill switch that immediately pulls both quotes out of the market, and others pause, resume and restart the stream of orders. Switches change the spread, and there is a mode where the board quotes around the live price of a real Polymarket market (read-only, no orders placed).

## How we built it

The board is a Lattice iCE40 HX4K: about 7,700 logic cells, no multipliers, and 16 KB of memory. LMSR needs logarithms and exponentials, so we rewrote the math to fit. The hard function is stored as a 2,048-entry lookup table, the liquidity and order-size settings are powers of two so every multiply and divide becomes a bit shift, and everything is whole numbers. One quote costs three table lookups, two subtractions and some shifting, and lands within 0.004 cents of the exact formula.

We wrote the spec first, then a Python reference model that generates 6,971 test requests with expected answers. The Verilog was checked against those in simulation before it ran on the board, then again on the board over a serial link. A C generator produces seeded streams of simulated traders (some random, some who know the true probability), and an exchange simulator replays the same stream into each version and tracks inventory and profit. Each version times the Tick-to-Quote latency, so the comparison leaves out the cable.

## Challenges we ran into

The biggest challenge was learning Verilog and FPGAs from the ground up. Both of us had worked almost entirely in software before this, and hardware required us to think about a program very differently. Every part of the circuit is active at the same time, and you have to decide what happens on each clock tick and what has to wait for the next one. It was a big step outside our comfort zone and also the most rewarding part of the project.

Also, getting three implementations to match on every bit was harder than getting any one of them to work. Rounding behaves differently across Verilog, C++ and Python, so the spec had to pin down every shift and rounding direction.

## Accomplishments that we're proud of

We accomplished 188 ns latency and zero jitter, measured on the real FPGA hardware and not just in simulation. All three versions agree byte for byte across 6,971 test requests and four full simulated markets. Finally, the whole design fits in about a quarter of a hobby-grade chip that has no multiplier.

## What we learned

Mostly we learned how to design hardware, coming from a software background. In software you write steps that run one after another. In Verilog you describe a circuit where everything happens in parallel, and the clock decides when results move from one stage to the next. Once we understood that, ideas like pipelining made sense: if a step is too slow to finish in one clock tick, you split it in two and store the intermediate result. We also learned to simulate everything before putting it on the board, because a bug in a waveform is much easier to find than a bug you can only see through a few LEDs.

The project also changed how we think about speed. We expected to compare average latency, but the more useful number turned out to be the worst case, and that is where the FPGA and the software versions actually differ.

## What's next for FPGA Prediction Market Maker

The real use case where an FPGA excels is wire to wire: orders arriving over Ethernet straight into the chip, with no laptop or USB link in between. That is where software slows down due to the operating system and network stack, and it is the comparison we most want to run. We would also like to trade against live market data with simulated fills and a profit figure, rather than only tracking the price, and repeat the benchmark with the laptop under load. Finally, we have a software prototype that extends the same lookup-table arithmetic to markets with more than two outcomes, and want to move it onto the board.
