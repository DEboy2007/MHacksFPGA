#!/usr/bin/env python3
"""Generate a dependency-free benchmark dashboard from bench/results.json."""
import argparse
import csv
import json
import pathlib


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
    cards = []
    for result in data["results"]:
        stats = result["latency"]
        cards.append(
            f"<article><h2>{result['name']}</h2>"
            f"<p>{result['samples']} samples · {result['latency_unit']}</p>"
            f"<dl><dt>Median</dt><dd>{stats['median']}</dd>"
            f"<dt>p99</dt><dd>{stats['p99']}</dd>"
            f"<dt>p99.9</dt><dd>{stats['p99_9']}</dd>"
            f"<dt>Max</dt><dd>{stats['max']}</dd></dl></article>"
        )
    payload = json.dumps({"summary": data, "series": series})
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>LMSR benchmark</title>
<style>
body{{font:16px system-ui;background:#101827;color:#e7edf7;max-width:1100px;margin:40px auto;padding:0 20px}}
h1{{font-size:32px}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}}
article{{background:#18243a;border:1px solid #31415e;border-radius:12px;padding:18px}}
dl{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}dt{{color:#9db0cc}}dd{{margin:0;text-align:right;font-weight:700}}
small{{color:#9db0cc}} code{{color:#8ee6c1}}
</style></head><body>
<h1>Hardware LMSR market maker</h1>
<p><small>Same event file: <code>{data['events']}</code> · paced at {data['rate']} requests/s</small></p>
<section class="grid">{''.join(cards)}</section>
<h2>Replay</h2><canvas id="chart" width="1040" height="360"></canvas>
<h2>Replay notes</h2><p>Latency is compute latency reported by each market maker.
FPGA values are clock counts; CPU values are nanoseconds. Round-trip transport
latency is intentionally not mixed into these cards.</p>
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
