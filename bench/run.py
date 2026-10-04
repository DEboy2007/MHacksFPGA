#!/usr/bin/env python3
"""Run reproducible M5 latency benchmarks and emit dashboard data.

The exchange remains the source of truth for pacing, fills, P&L and quote
agreement. This wrapper runs each market maker against the same event file and
extracts the reported compute latency from its CSV log.
"""
import argparse
import json
import math
import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1)
    return ordered[index]


def run_one(name, command, events, out, rate):
    log = out / f"{name}.csv"
    quotes = out / f"{name}.quotes"
    started = time.monotonic_ns()
    result = subprocess.run(
        [str(ROOT / "exchange" / "exchange"), "--events", str(events),
         "--mm", command, "--quotes", str(quotes), "--log", str(log),
         "--rate", str(rate)],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    elapsed = (time.monotonic_ns() - started) / 1e9
    rows = log.read_text().splitlines()
    header = rows[0].split(",")
    latency_index = header.index("mm_latency")
    action_index = header.index("action")
    # The exchange emits a latency for every fill request. Zero is a real
    # measurement on a coarse host timer, not a missing sample.
    latencies = [int(fields[latency_index]) for row in rows[1:]
                 for fields in [row.split(",")]
                 if int(fields[action_index]) != 0]
    return {
        "name": name,
        "elapsed_s": elapsed,
        "latency_unit": "ns" if name.startswith(("python", "cpp")) else "clocks",
        "samples": len(latencies),
        "latency": {
            "median": percentile(latencies, 0.50),
            "p99": percentile(latencies, 0.99),
            "p99_9": percentile(latencies, 0.999),
            "max": max(latencies) if latencies else None,
        },
        "histogram": [{"value": value, "count": latencies.count(value)}
                      for value in sorted(set(latencies))],
        "exchange_output": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "log": str(log.relative_to(ROOT)),
        "quotes": str(quotes.relative_to(ROOT)),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path, default=ROOT / "out" / "bench")
    parser.add_argument("--rate", type=float, default=400,
                        help="requests per second; 400 is below the UART budget")
    parser.add_argument("--fpga-port")
    parser.add_argument("--unthrottled", action="store_true",
                        help="also run CPU versions without pacing")
    args = parser.parse_args()
    out = args.out if args.out.is_absolute() else ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)

    events = args.events
    if events is None:
        events = out / "benchmark.events"
        subprocess.run(
            [str(ROOT / "gen" / "gen"), "-o", str(events), "-n", "4000",
             "-seed", "270", "-p0", "0.35", "-news", "2000:0.75",
             "-informed", "0.25"],
            cwd=ROOT, check=True,
        )
    elif not events.is_absolute():
        events = ROOT / events

    targets = [("cpp", "cmd:mm_cpp/mm_cpp"), ("python", "cmd:python3 mm_py/main.py")]
    if args.fpga_port:
        targets.append(("fpga", f"serial:{args.fpga_port}"))
    results = []
    rates = [args.rate] + ([0] if args.unthrottled else [])
    for rate in rates:
        suffix = "unthrottled" if rate == 0 else "paced"
        for name, command in targets:
            results.append(run_one(f"{name}-{suffix}", command, events, out, rate))

    by_mode = {}
    for result in results:
        mode = result["name"].rsplit("-", 1)[-1]
        by_mode.setdefault(mode, []).append(result)
    agreement = {}
    for mode, mode_results in by_mode.items():
        baseline = pathlib.Path(ROOT / mode_results[0]["quotes"]).read_bytes()
        agreement[mode] = all(
            pathlib.Path(ROOT / result["quotes"]).read_bytes() == baseline
            for result in mode_results[1:]
        )
        if not agreement[mode]:
            raise SystemExit(f"quote mismatch in {mode} benchmark")
    for result in results:
        print("{}: {} samples, median={}, p99={}, p99.9={}, max={} {}".format(
            result["name"], result["samples"], result["latency"]["median"],
            result["latency"]["p99"], result["latency"]["p99_9"],
            result["latency"]["max"], result["latency_unit"]))
    data = {"events": str(events.relative_to(ROOT)), "rate": args.rate,
            "generated_at": time.time(), "quote_agreement": agreement,
            "results": results}
    (out / "results.json").write_text(json.dumps(data, indent=2) + "\n")
    dashboard = ROOT / "dashboard" / "dashboard.py"
    subprocess.run(["python3", str(dashboard), "--data", str(out / "results.json"),
                    "--output", str(out / "index.html")], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
