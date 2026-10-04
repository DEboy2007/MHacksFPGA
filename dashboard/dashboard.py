#!/usr/bin/env python3
"""Generate a dependency-free benchmark dashboard from bench/results.json."""
import argparse
import csv
import json
import pathlib


SERIES = {"fpga": ("FPGA", "var(--series-1)"), "cpp": ("C++", "var(--series-2)"),
          "python": ("Python", "var(--series-3)")}
STATS = (("median", "Median"), ("p99", "p99"), ("p99_9", "p99.9"), ("max", "Max"))


def to_ns(result, clock_hz):
    """Latency stats in nanoseconds (the FPGA reports clock ticks)."""
    scale = 1e9 / clock_hz if result["latency_unit"] == "clocks" else 1.0
    return {key: result["latency"][key] * scale for key, _ in STATS}


def fmt_ns(ns):
    return f"{ns / 1000:.2f} µs" if ns >= 1000 else f"{ns:.0f} ns"


def latency_table(rows):
    body = "".join(
        f"<tr><th scope='row'><span class='key' style='background:{color}'></span>{label}"
        f"<small>{note}</small></th>"
        + "".join(f"<td>{fmt_ns(ns[key])}</td>" for key, _ in STATS)
        + f"<td>{ns['max'] / ns['median']:.0f}×</td></tr>"
        for label, color, note, ns in rows)
    head = "".join(f"<th>{name}</th>" for _, name in STATS)
    return (f"<table class='lat'><thead><tr><th>Version</th>{head}<th>Max ÷ median</th></tr></thead>"
            f"<tbody>{body}</tbody></table>")


def latency_chart(rows):
    """One row per version on a shared log axis: a line from median to max with
    a marker at each statistic. A version with no jitter collapses to one dot."""
    import math
    width, left, right, row_h, top = 1040, 96, 150, 64, 34
    lo, hi = 1, 5                                    # 10 ns .. 100 µs
    height = top + row_h * len(rows) + 40

    def x(ns):
        pos = (math.log10(max(ns, 10 ** lo)) - lo) / (hi - lo)
        return left + min(1.0, pos) * (width - left - right)

    out = [f"<svg viewBox='0 0 {width} {height}' role='img' "
           f"aria-label='Compute latency per reply: median, p99, p99.9 and max for each version'>"]
    for exp in range(lo, hi + 1):                    # recessive grid + axis labels
        gx = x(10 ** exp)
        out.append(f"<line x1='{gx:.1f}' y1='{top - 8}' x2='{gx:.1f}' y2='{height - 30}' class='grid'/>")
        out.append(f"<text x='{gx:.1f}' y='{height - 10}' class='tick' text-anchor='middle'>"
                   f"{fmt_ns(10 ** exp)}</text>")
    shapes = {"median": "M-6,0a6,6 0 1,0 12,0a6,6 0 1,0 -12,0", "p99": "M0,-7L7,0L0,7L-7,0Z",
              "p99_9": "M0,-7L7,6L-7,6Z", "max": "M-3,-8h6v16h-6Z"}
    for i, (label, color, note, ns) in enumerate(rows):
        y = top + row_h * i + row_h / 2
        out.append(f"<text x='{left - 14}' y='{y + 5:.1f}' class='rowlabel' text-anchor='end'>{label}</text>")
        flat = ns["max"] == ns["median"]
        if not flat:
            out.append(f"<line x1='{x(ns['median']):.1f}' y1='{y}' x2='{x(ns['max']):.1f}' y2='{y}' "
                       f"stroke='{color}' stroke-width='2'/>")
        for key, name in (STATS[:1] if flat else STATS):
            out.append(f"<path d='{shapes[key]}' transform='translate({x(ns[key]):.1f},{y})' fill='{color}' "
                       f"class='mark'><title>{label} {name}: {fmt_ns(ns[key])}</title></path>")
        if flat:
            out.append(f"<text x='{x(ns['median']) + 14:.1f}' y='{y + 5:.1f}' class='val'>"
                       f"{fmt_ns(ns['median'])} on every reply</text>")
        else:
            out.append(f"<text x='{x(ns['median']):.1f}' y='{y - 14:.1f}' class='val' text-anchor='middle'>"
                       f"{fmt_ns(ns['median'])}</text>")
            out.append(f"<text x='{x(ns['max']) + 14:.1f}' y='{y + 5:.1f}' class='val'>{fmt_ns(ns['max'])}</text>")
    out.append("</svg>")
    key = "".join(f"<span><svg viewBox='-9 -9 18 18' width='16' height='16'><path d='{shapes[k]}' class='keymark'/>"
                  f"</svg>{name}</span>" for k, name in STATS)
    return "".join(out) + f"<p class='legend'>{key}</p>"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    data = json.loads(pathlib.Path(args.data).read_text())
    root = pathlib.Path(__file__).resolve().parent.parent
    series = {}
    for result in data["results"]:
        path = root / result["log"]
        if not path.exists():
            continue
        with path.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        series[result["name"]] = [
            {"event": int(row["event"]), "mid": (
                (int(row["bid"]) + int(row["ask"])) / 2 if row["bid"] and row["ask"]
                else int(row["bid"] or row["ask"] or 0)
            ), "truth": int(row["truth_bp"]) / 100,
             "inventory": -int(row["d"]),
             "pnl": float(row["mtm"]) / 100}
            for row in rows
        ]
    # Latency: one table and one chart per benchmark mode (paced / unthrottled).
    clock_hz = data.get("fpga_clock_hz", 12e6)       # results from before the PLL ran at 12 MHz
    sections = []
    modes = []
    for result in data["results"]:
        mode = result["name"].rsplit("-", 1)[-1]
        if mode not in modes:
            modes.append(mode)
    for mode in modes:
        rows = []
        for key in ("fpga", "cpp", "python"):        # fixed order, so colours never move
            for result in data["results"]:
                if result["name"] == f"{key}-{mode}":
                    label, color = SERIES[key]
                    note = f"{result['samples']} replies"
                    if result["latency_unit"] == "clocks":
                        note += f", {result['latency']['median']} clocks at {clock_hz / 1e6:.0f} MHz"
                    rows.append((label, color, note, to_ns(result, clock_hz)))
        title = "paced at %g requests/s" % data["rate"] if mode == "paced" else mode
        sections.append(f"<h2>Compute latency per reply ({title})</h2>"
                        f"{latency_table(rows)}<div class='viz-root'>{latency_chart(rows)}</div>")
    payload = json.dumps({"summary": data, "series": series})
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>LMSR benchmark</title>
<style>
body{{font:16px system-ui;background:#101827;color:#e7edf7;max-width:1100px;margin:40px auto;padding:0 20px}}
h1{{font-size:32px}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}}
article{{background:#18243a;border:1px solid #31415e;border-radius:12px;padding:18px}}
dl{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}dt{{color:#9db0cc}}dd{{margin:0;text-align:right;font-weight:700}}
small{{color:#9db0cc}} code{{color:#8ee6c1}}
:root{{--surface-1:#1a1a19;--text-primary:#ffffff;--text-secondary:#c3c2b7;--grid:#3a3a37;
  --series-1:#3987e5;--series-2:#d95926;--series-3:#199e70}}
.viz-root{{background:var(--surface-1);border-radius:12px;padding:16px 8px 8px;margin:16px 0 32px}}
.viz-root svg{{width:100%;height:auto;display:block}}
.viz-root .grid{{stroke:var(--grid);stroke-width:1}}
.viz-root .tick{{fill:var(--text-secondary);font-size:13px}}
.viz-root .rowlabel{{fill:var(--text-primary);font-size:16px;font-weight:600}}
.viz-root .val{{fill:var(--text-primary);font-size:14px}}
.viz-root .mark{{stroke:var(--surface-1);stroke-width:2}}
.viz-root .legend{{display:flex;gap:24px;justify-content:center;color:var(--text-secondary);font-size:14px;margin:4px 0}}
.viz-root .legend span{{display:inline-flex;align-items:center;gap:6px}}
.viz-root .legend svg{{width:16px;height:16px}} .viz-root .keymark{{fill:var(--text-secondary)}}
table.lat{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}
table.lat th,table.lat td{{padding:10px 12px;border-bottom:1px solid #31415e;text-align:right}}
table.lat th:first-child{{text-align:left}} table.lat thead th{{color:#9db0cc;font-weight:500}}
table.lat tbody th{{font-weight:700}} table.lat small{{display:block;font-weight:400}}
table.lat .key{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px}}
</style></head><body>
<h1>Hardware LMSR market maker</h1>
<p><small>Same event file: <code>{data['events']}</code> · paced at {data['rate']} requests/s</small></p>
{''.join(sections)}
<h2>Replay</h2><canvas id="chart" width="1040" height="360"></canvas>
<h2>Notes</h2><p>Latency is the compute time each market maker reports for itself:
from the last byte of a request arriving to the new quote being ready. Transport time is
excluded for all three. The axis is logarithmic. The FPGA reports clock ticks, converted to
nanoseconds here; the C++ median is one tick of this Mac's timer (about 42 ns), so its true
median is at or below that.</p>
<script>
const benchmark = {payload};
const canvas = document.getElementById("chart"), ctx = canvas.getContext("2d");
const names = Object.keys(benchmark.series), points = benchmark.series[names[0]] || [];
const w=canvas.width,h=canvas.height,pad=35;
ctx.fillStyle="#18243a";ctx.fillRect(0,0,w,h);
function line(key,color){{ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();
  points.forEach((_,i)=>{{const x=pad+(w-2*pad)*i/Math.max(1,points.length-1);
  const values=points.map(p=>p[key]), min=Math.min(...values), max=Math.max(...values);
  const y=h-pad-(h-2*pad)*(points[i][key]-min)/Math.max(1,max-min);
  i?ctx.lineTo(x,y):ctx.moveTo(x,y);}});ctx.stroke();}}
line("mid","#8ee6c1"); line("truth","#ffcb6b");
ctx.fillStyle="#e7edf7";ctx.fillText("mid",pad,18);ctx.fillStyle="#ffcb6b";ctx.fillText("truth",pad+55,18);
</script>
</body></html>"""
    pathlib.Path(args.output).write_text(html)


if __name__ == "__main__":
    main()
