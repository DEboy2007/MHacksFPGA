# MHacksFPGA

A market maker for binary prediction markets, priced with Hanson's LMSR and
built three ways: on an FPGA (iCE40 HX4K "HailBoard"), in C++ and in Python.
All three produce identical quotes for the same order stream.

- `docs/spec.md`: the fixed-point spec, wire protocol and file formats. `docs/lmsr.pdf`: the math.
- `golden/`: Python golden model and test vectors. `tools/gen_tables.py`: the lookup table.
- `fpga/`: Verilog (APIO project). `mm_cpp/`, `mm_py/`: the software market makers.
- `gen/`: seeded order-flow generator. `exchange/`: exchange simulator.

## Running things

    make                      # build gen, exchange, mm_cpp
    make check                # same order streams into C++, Python and the FPGA; quotes must be identical
    fpga/run_tests.sh         # Verilog testbenches (Icarus Verilog)
    cd fpga && apio upload    # build and flash the market maker
    python3 tools/replay.py   # golden vectors against the board (--cmd mm_cpp/mm_cpp for software)
    make demo                 # then press KEY0: replays a fixed order stream you can watch (RATE=10 trades/s)

The FPGA's serial port is `/dev/cu.usbmodem2103`. KEY0 on the board restarts the market maker; with `make demo` running, it restarts the whole run.
