#!/bin/sh
# M4 check: replay the same order streams into the C++, Python and FPGA market
# makers and require byte-identical quote streams.
#   tools/check_identical.sh            # all three (board must be plugged in)
#   PORT=none tools/check_identical.sh  # C++ and Python only
set -e
cd "$(dirname "$0")/.."
PORT="${PORT:-/dev/cu.usbmodem2103}"
mkdir -p out

run() {   # name, then generator options
    name="$1"; shift
    echo "=== $name: gen $*"
    gen/gen -o "out/$name.events" "$@"
    for mm in cpp py fpga; do
        case $mm in
            cpp)  target="cmd:mm_cpp/mm_cpp" ;;
            py)   target="cmd:python3 mm_py/main.py" ;;
            fpga) [ "$PORT" = none ] && continue; target="serial:$PORT" ;;
        esac
        printf '%-5s ' "$mm"
        exchange/exchange --events "out/$name.events" --mm "$target" \
            --quotes "out/$name.$mm.quotes" --log "out/$name.$mm.csv" | head -1
    done
    cmp "out/$name.cpp.quotes" "out/$name.py.quotes"
    [ "$PORT" = none ] || cmp "out/$name.cpp.quotes" "out/$name.fpga.quotes"
    exchange/exchange --events "out/$name.events" --mm "cmd:mm_cpp/mm_cpp" | tail -2
    echo "identical: $(wc -c < "out/$name.cpp.quotes" | tr -d ' ') bytes of quotes"
}

run news    -n 4000 -seed 1 -p0 0.35 -news 2000:0.75 -informed 0.25
run noise   -n 3000 -seed 2 -p0 0.50 -informed 0
run sharp   -n 3000 -seed 3 -p0 0.90 -informed 0.6 -lb 6 -ls 2 -hs 1
run wide    -n 3000 -seed 4 -p0 0.20 -news 1000:0.60 -informed 0.3 -lb 7 -ls 5 -hs 3
echo "ALL IDENTICAL"
