#!/bin/sh
# Run every testbench with Icarus Verilog. Run from anywhere; exits non-zero
# on the first failure. (top_tb takes about 30 seconds.)
set -e
cd "$(dirname "$0")"
mkdir -p _build
for tb in echo_top_tb lmsr_core_tb top_tb; do
    echo "== $tb"
    iverilog -g2012 -o "_build/$tb.vvp" -s "$tb" *.v
    vvp -n "_build/$tb.vvp" | grep -v '\$finish'
done
