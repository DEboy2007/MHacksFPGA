# Host-side build: order-flow generator, exchange simulator, C++ market maker.
CC       = cc
CXX      = c++
CFLAGS   = -O2 -Wall -Wextra -std=c11
CXXFLAGS = -O2 -Wall -Wextra -std=c++17

BINS = gen/gen exchange/exchange mm_cpp/mm_cpp

all: $(BINS)

gen/gen: gen/gen.c
	$(CC) $(CFLAGS) -o $@ $<

exchange/exchange: exchange/exchange.cpp
	$(CXX) $(CXXFLAGS) -o $@ $<

mm_cpp/mm_cpp: mm_cpp/main.cpp mm_cpp/lmsr.hpp
	$(CXX) $(CXXFLAGS) -o $@ $<

# M4 check: all three market makers must produce identical quote streams.
check: all
	tools/check_identical.sh

# Board demo: press KEY0 to (re)play a fixed order stream at a watchable pace.
# 9999 orders, so the board's four-digit fill counter never wraps.
PORT ?= /dev/cu.usbmodem2103
RATE ?= 200
demo: all
	mkdir -p out
	gen/gen -o out/demo.events -n 9999 -seed 7 -p0 0.35 -news 5000:0.75 -informed 0.3
	exchange/exchange --events out/demo.events --mm serial:$(PORT) --demo --rate $(RATE)

# Live demo: the market maker quotes around a real Polymarket market's price.
# Read-only public data; no orders are placed. MARKET=slug picks a market.
# Needs: python3 -m pip install -r requirements-polymarket.txt
MM ?= serial:$(PORT)
live: all
	exchange/exchange --source 'cmd:python3 tools/polymarket_paper.py --exchange-source $(if $(MARKET),--market $(MARKET))' --mm '$(MM)'

clean:
	rm -f $(BINS)
	rm -rf out

.PHONY: all check demo live clean
