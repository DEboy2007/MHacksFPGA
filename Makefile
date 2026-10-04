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

clean:
	rm -f $(BINS)
	rm -rf out

.PHONY: all check clean
