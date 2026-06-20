# ruff: noqa: E501
#!/usr/bin/env python3
"""Self-contained HTML dashboard for hpm memory store.

Generates a single HTML file with Chart.js from CDN — no server needed.
Open directly in a browser.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from . import db as db_module

DASHBOARD_PATH = "~/.hpm/dashboard.html"


def _score_distribution(conn: "sqlite3.Connection") -> dict[str, int]:
    """Count entries in score ranges: 0-.25, .25-.5, .5-.75, .75-1."""
    buckets = {
        "0.00-0.25": 0,
        "0.25-0.50": 0,
        "0.50-0.75": 0,
        "0.75-1.00": 0,
    }
    rows = conn.execute(
        "SELECT decay_score FROM memories"
    ).fetchall()
    for row in rows:
        s = row["decay_score"]
        if s < 0.25:
            buckets["0.00-0.25"] += 1
        elif s < 0.50:
            buckets["0.25-0.50"] += 1
        elif s < 0.75:
            buckets["0.50-0.75"] += 1
        else:
            buckets["0.75-1.00"] += 1
    return buckets


def _recent_entries(conn: "sqlite3.Connection", limit: int = 50) -> list[dict[str, object]]:
    """Fetch recent active entries for the table."""
    rows = conn.execute(
        "SELECT id, content, source, timestamp, decay_score, tags, "
        "last_accessed, session_id FROM memories "
        "ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def generate(conn: "sqlite3.Connection", output_path: str = DASHBOARD_PATH) -> str:
    """Generate the dashboard HTML and write to *output_path*.

    Returns the path to the generated file.
    """
    stats = db_module.store_stats(conn)
    dist = _score_distribution(conn)
    entries = _recent_entries(conn)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    dist_json = json.dumps(dist)
    entries_json = json.dumps(entries, default=str)
    sources_str = ", ".join(stats['sources'])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>hpm Memory Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0d1117; color: #e6edf3; padding: 24px; }}
  h1 {{ font-size: 24px; margin-bottom: 4px; }}
  .subtitle {{ color: #8b949e; font-size: 14px; margin-bottom: 20px; }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 12px; margin-bottom: 24px; }}
  .stat-card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
                padding: 16px; }}
  .stat-card .value {{ font-size: 28px; font-weight: 700; color: #58a6ff; }}
  .stat-card .label {{ font-size: 12px; color: #8b949e; text-transform: uppercase; }}
  .charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }}
  .chart-box {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }}
  .chart-box h2 {{ font-size: 14px; margin-bottom: 12px; color: #58a6ff; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th {{ background: #161b22; border: 1px solid #30363d; padding: 8px 12px;
        text-align: left; font-weight: 600; color: #58a6ff; position: sticky; top: 0; }}
  td {{ border: 1px solid #30363d; padding: 8px 12px; vertical-align: top; }}
  tr:hover {{ background: #1c2333; }}
  .score-low {{ color: #f85149; }}
  .score-mid {{ color: #d29922; }}
  .score-high {{ color: #3fb950; }}
  .tag {{ display: inline-block; background: #1c2333; border: 1px solid #30363d;
          border-radius: 4px; padding: 1px 6px; font-size: 11px; margin: 1px; }}
  .src {{ font-size: 11px; color: #8b949e; }}
  .filter-bar {{ margin-bottom: 12px; display: flex; gap: 8px; }}
  .filter-bar input, .filter-bar select {{ background: #0d1117; border: 1px solid #30363d;
    color: #e6edf3; padding: 6px 10px; border-radius: 6px; font-size: 13px; }}
  .table-wrap {{ max-height: 600px; overflow-y: auto; border: 1px solid #30363d;
                 border-radius: 8px; }}
</style>
</head>
<body>

<h1>hpm Memory Dashboard</h1>
<p class="subtitle">Generated {now}</p>

<div class="stats">
  <div class="stat-card">
    <div class="value">{stats['total']}</div>
    <div class="label">Total Entries</div>
  </div>
  <div class="stat-card">
    <div class="value">{stats['entries_below_eviction']}</div>
    <div class="label">Below Eviction Threshold</div>
  </div>
    <div class="stat-card">
    <div class="value">{sources_str}</div>
    <div class="label">Sources</div>
  </div>
</div>

<div class="charts">
  <div class="chart-box">
    <h2>Score Distribution</h2>
    <canvas id="distChart" height="200"></canvas>
  </div>
  <div class="chart-box">
    <h2>Score vs. Recency</h2>
    <canvas id="scatterChart" height="200"></canvas>
  </div>
</div>

<div class="filter-bar">
  <input type="text" id="searchInput" placeholder="Search content..." oninput="filterTable()">
  <select id="sourceFilter" onchange="filterTable()">
    <option value="">All sources</option>
  </select>
  <select id="scoreFilter" onchange="filterTable()">
    <option value="">All scores</option>
    <option value="low">Low (0-0.25)</option>
    <option value="mid">Medium (0.25-0.75)</option>
    <option value="high">High (0.75-1.0)</option>
  </select>
</div>

<div class="table-wrap">
<table><thead><tr>
  <th>Content</th><th>Score</th><th>Source</th><th>Created</th><th>Tags</th>
</tr></thead><tbody id="entriesBody"></tbody></table>
</div>

<script>
const entries = {entries_json};

function scoreClass(s) {{
  if (s < 0.25) return 'score-low';
  if (s < 0.75) return 'score-mid';
  return 'score-high';
}}

function scoreColor(s) {{
  if (s < 0.25) return '#f85149';
  if (s < 0.75) return '#d29922';
  return '#3fb950';
}}

// Score Distribution Chart
new Chart(document.getElementById('distChart'), {{
  type: 'bar',
  data: {{
    labels: Object.keys({dist_json}),
    datasets: [{{
      label: 'Entries',
      data: Object.values({dist_json}),
      backgroundColor: ['#f85149', '#d29922', '#58a6ff', '#3fb950'],
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ y: {{ beginAtZero: true, ticks: {{ stepSize: 1 }} }} }}
  }}
}});

// Scatter: score vs age
const ages = entries.map(e => {{
  const t = new Date(e.timestamp);
  return (Date.now() - t) / 86400000;
}});
const scores = entries.map(e => e.decay_score);

new Chart(document.getElementById('scatterChart'), {{
  type: 'scatter',
  data: {{
    datasets: [{{
      label: 'Memories',
      data: entries.map((e, i) => ({{x: ages[i], y: scores[i]}})),
      backgroundColor: scores.map(s => scoreColor(s)),
      pointRadius: 5,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ title: {{ display: true, text: 'Age (days)', color: '#8b949e' }} }},
      y: {{ title: {{ display: true, text: 'Decay Score', color: '#8b949e' }}, min: 0, max: 1 }}
    }}
  }}
}});

// Populate source filter
const sources = new Set();
entries.forEach(e => {{
  try {{ JSON.parse(e.source).forEach(s => sources.add(s)); }}
  catch {{ sources.add(e.source); }}
}});
const sourceSelect = document.getElementById('sourceFilter');
sources.forEach(s => {{
  const opt = document.createElement('option');
  opt.value = s; opt.textContent = s;
  sourceSelect.appendChild(opt);
}});

function renderEntries(filtered) {{
  const tbody = document.getElementById('entriesBody');
  tbody.innerHTML = '';
  filtered.forEach(e => {{
    const tr = document.createElement('tr');
    let src = e.source || '';
    try {{ src = JSON.parse(src).join(', '); }} catch {{}}
    let tags = '';
    try {{ tags = JSON.parse(e.tags || '[]').map(t => `<span class="tag">${{t}}</span>`).join(''); }}
    catch {{}}
    tr.innerHTML = `
      <td>${{e.content}}</td>
      <td class="${{scoreClass(e.decay_score)}}">${{e.decay_score.toFixed(3)}}</td>
      <td class="src">${{src}}</td>
      <td class="src">${{e.timestamp ? e.timestamp.slice(0, 10) : ''}}</td>
      <td>${{tags}}</td>
    `;
    tbody.appendChild(tr);
  }});
}}

function filterTable() {{
  const q = document.getElementById('searchInput').value.toLowerCase();
  const srcF = document.getElementById('sourceFilter').value;
  const scoreF = document.getElementById('scoreFilter').value;
  const filtered = entries.filter(e => {{
    if (q && !e.content.toLowerCase().includes(q)) return false;
    if (srcF) {{
      try {{ if (!JSON.parse(e.source).includes(srcF)) return false; }}
      catch {{ if (e.source !== srcF) return false; }}
    }}
    if (scoreF === 'low' && e.decay_score >= 0.25) return false;
    if (scoreF === 'mid' && (e.decay_score < 0.25 || e.decay_score >= 0.75)) return false;
    if (scoreF === 'high' && e.decay_score < 0.75) return false;
    return true;
  }});
  renderEntries(filtered);
}}

renderEntries(entries);
</script>
</body>
</html>"""
    from pathlib import Path

    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
    return str(path)
