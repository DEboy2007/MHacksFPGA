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
PORT ?= /dev/cu.usbmodem2103
RATE ?= 10
demo: all
	mkdir -p out
	gen/gen -o out/demo.events -n 600 -seed 7 -p0 0.35 -news 300:0.75 -informed 0.3
	exchange/exchange --events out/demo.events --mm serial:$(PORT) --demo --rate $(RATE)

clean:
	rm -f $(BINS)
	rm -rf out

.PHONY: all check demo clean
