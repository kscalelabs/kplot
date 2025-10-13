import os
import json
import glob
from typing import Dict, List, Tuple, Any
from flask import Flask, jsonify, request, Response


app = Flask(__name__)


class DataSource:
    def __init__(self, label: str, path: str) -> None:
        self.label: str = label
        self.path: str = path
        # series_name -> list of (step_id, value)
        self.series_to_points: Dict[str, List[Tuple[int, float]]] = {}

    def add_point(self, series_name: str, step_id: int, value: float) -> None:
        if series_name not in self.series_to_points:
            self.series_to_points[series_name] = []
        self.series_to_points[series_name].append((step_id, value))


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def extract_series_from_record(record: Dict[str, Any], joint_names: List[str]) -> Dict[str, float]:
    series: Dict[str, float] = {}
    # Keys whose list elements correspond to joints in JOINT_NAMES
    JOINTED_KEYS = {
        "joint_angles",
        "joint_velocities",
        "joint_amps",
        "joint_torques",
        "joint_temps",
        "output",
        "action",
        # Newly supported optional fields
        "torque_commands",
        "torque_diff",
    }
    for key, value in record.items():
        # skip None values entirely
        if value is None:
            continue
        # do not include step_id as a series; it will be used as x-axis
        if key == "step_id":
            continue
        # include t_us; many users want to see timestamp drift
        if is_number(value):
            series[key] = float(value)
        elif isinstance(value, list):
            # Map joint-indexed arrays to named joints (except commands)
            if key in JOINTED_KEYS and key != "command":
                for idx, v in enumerate(value):
                    if is_number(v):
                        joint_label = joint_names[idx] if idx < len(joint_names) else f"idx{idx}"
                        series[f"{key}.{joint_label}"] = float(v)
            else:
                for idx, v in enumerate(value):
                    if is_number(v):
                        series[f"{key}[{idx}]"] = float(v)
                # skip non-numeric entries inside lists
        # skip non-numeric, non-list entries (e.g., nested objects or strings)
    return series


def load_data_sources(root_dir: str) -> Tuple[List[DataSource], List[str]]:
    # Search for .ndjson files recursively in root_dir
    # If root_dir has a 'data' subdirectory, use that; otherwise use root_dir directly
    data_dir = os.path.join(root_dir, "data") if os.path.isdir(os.path.join(root_dir, "data")) else root_dir
    ndjson_paths: List[str] = []
    if os.path.isdir(data_dir):
        # Prefer specific kinfer_log.ndjson names, but accept any .ndjson
        kinfer_specific = glob.glob(os.path.join(data_dir, "**", "kinfer_log.ndjson"), recursive=True)
        any_ndjson = glob.glob(os.path.join(data_dir, "**", "*.ndjson"), recursive=True)
        # Combine and deduplicate
        ndjson_paths = sorted({*kinfer_specific, *any_ndjson})
    data_sources: List[DataSource] = []

    for ndjson_path in ndjson_paths:
        # Label with bot name and session for clarity
        try:
            label = os.path.relpath(ndjson_path, data_dir)
            # Make labels more readable: extract bot name from path
            # e.g., kd-1/ecstatic_jackson_20251013_110106/kinfer_log.ndjson -> kd-1/ecstatic_jackson...
            parts = label.split(os.sep)
            if len(parts) >= 2:
                label = f"{parts[0]}/{parts[1]}"
        except Exception:
            label = os.path.basename(ndjson_path)
        ds = DataSource(label=label, path=ndjson_path)
        # Parse NDJSON
        try:
            with open(ndjson_path, "r", encoding="utf-8") as f:
                joint_names: List[str] = []
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    # Update joint_names if present in the record
                    if isinstance(record.get("joint_order"), list):
                        # Use the provided names as-is
                        joint_names = [str(x) for x in record["joint_order"]]
                    step_id = record.get("step_id")
                    if not isinstance(step_id, int):
                        # attempt to coerce
                        try:
                            step_id = int(step_id)
                        except Exception:
                            # if there's no sane step id, skip the record
                            continue
                    flat_series = extract_series_from_record(record, joint_names)
                    for s_name, s_val in flat_series.items():
                        ds.add_point(s_name, step_id, s_val)
        except FileNotFoundError:
            continue

        # Sort points by step_id for each series to ensure monotonic x
        for s_name, points in ds.series_to_points.items():
            points.sort(key=lambda p: p[0])
        data_sources.append(ds)

    # Build master list of all series names across sources
    all_series_names: List[str] = sorted({s for ds in data_sources for s in ds.series_to_points.keys()})
    return data_sources, all_series_names


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_SOURCES, ALL_SERIES = load_data_sources(ROOT_DIR)
SOURCE_LABELS: List[str] = [ds.label for ds in DATA_SOURCES]


@app.route("/")
def index() -> Response:
    # Serve a single-page app with Plotly and offset controls
    html = f"""
<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Kinfer NDJSON Viewer</title>
    <script src=\"https://cdn.plot.ly/plotly-2.27.0.min.js\"></script>
    <style>
      body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; padding: 0 16px; }}
      header {{ position: sticky; top: 0; background: #fff; padding: 12px 0; z-index: 10; border-bottom: 1px solid #eee; }}
      .controls {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }}
      .control-group {{ display: flex; align-items: center; gap: 6px; }}
      .charts {{ padding: 16px 0 36px; }}
      .category {{ margin: 20px 0 24px; }}
      .category h2 {{ margin: 0 0 10px; font-size: 16px; font-weight: 700; color: #333; }}
      .category-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 18px; }}
      .chart {{ border: 1px solid #e6e6e6; border-radius: 8px; padding: 8px; background: #fafafa; }}
      .chart h3 {{ margin: 4px 0 8px; font-size: 14px; font-weight: 600; }}
      input[type=number] {{ width: 100px; padding: 6px 8px; }}
      button {{ padding: 8px 12px; border: 1px solid #ddd; background: #fff; border-radius: 6px; cursor: pointer; }}
      button:hover {{ background: #f3f3f3; }}
      .legend-label {{ display:inline-block; padding:2px 6px; border-radius:4px; border:1px solid #ddd; margin-right:6px; font-size:12px; }}
      .src-toggle {{ cursor: pointer; }}
      .src-off {{ opacity: 0.35; }}
      #search {{ padding: 6px 8px; min-width: 220px; }}
    </style>
  </head>
  <body>
    <header>
      <div class=\"controls\">
        <div class=\"control-group\"> 
          <label for=\"search\"><strong>Search:</strong></label>
          <input id=\"search\" type=\"text\" placeholder=\"Filter titles...\" />
        </div>
        <div class=\"control-group\" id=\"source-toggles\">
          <strong>Sources:</strong>
          {''.join([f'<button class="legend-label src-toggle" type="button" data-index="{idx}">{idx}: {label}</button>' for idx, label in enumerate(SOURCE_LABELS)])}\n        </div>
        <div id=\"offset-controls\" class=\"control-group\"></div>
        <div class=\"control-group\">
          <button id=\"apply\">Apply offsets</button>
          <span id=\"status\"></span>
        </div>
      </div>
    </header>
    <main>
      <div id=\"charts\" class=\"charts\"></div>
    </main>

    <script>
      const sourceLabels = {json.dumps(SOURCE_LABELS)};
      const allSeries = {json.dumps(ALL_SERIES)};
      const categoriesOrder = [
        'Joint Angles',
        'Joint Velocities',
        'Joint Amps',
        'Joint Torques',
        'Torque Commands',
        'Torque Diff',
        'Joint Temperatures',
        'Action',
        'Output',
        'Commands',
        'Projected Gravity',
        'Gyroscope',
        'Accel',
        'Quaternion',
        'Other'
      ];

      function makeId(name) {{
        return 'chart-' + name.replace(/[^a-zA-Z0-9_\-]/g, '_');
      }}

      function categoryOf(name) {{
        if (name.startsWith('joint_angles.')) return 'Joint Angles';
        if (name.startsWith('joint_velocities.')) return 'Joint Velocities';
        if (name.startsWith('joint_amps.')) return 'Joint Amps';
        if (name.startsWith('joint_torques.')) return 'Joint Torques';
        if (name.startsWith('torque_commands.')) return 'Torque Commands';
        if (name.startsWith('torque_diff.')) return 'Torque Diff';
        if (name.startsWith('joint_temps.')) return 'Joint Temperatures';
        if (name.startsWith('action.')) return 'Action';
        if (name.startsWith('output.')) return 'Output';
        if (name.startsWith('command[')) return 'Commands';
        if (name.startsWith('projected_gravity[')) return 'Projected Gravity';
        if (name.startsWith('gyroscope[') || name.startsWith('gyro[')) return 'Gyroscope';
        if (name.startsWith('accel[')) return 'Accel';
        if (name.startsWith('quaternion[')) return 'Quaternion';
        if (name === 'timestamp_us' || name === 't_us') return 'Other';
        return 'Other';
      }}

      // Global per-source visibility state
      let globalSourceEnabled = sourceLabels.map(() => true);
      // Search/filter state and cached data
      let searchQuery = '';
      let lastSeriesData = null; // set after first fetch
      let searchDebounceHandle = null;

      function getFilteredNames(sourceNames) {{
        const q = (searchQuery || '').trim().toLowerCase();
        if (!q) return sourceNames.slice().sort();
        return sourceNames.filter((n) => n.toLowerCase().includes(q)).sort();
      }}

      function subsetSeriesData(seriesData, names) {{
        const out = {{}};
        names.forEach((n) => {{ if (seriesData[n]) out[n] = seriesData[n]; }});
        return out;
      }}

      function rerenderWithFilter() {{
        const names = lastSeriesData ? getFilteredNames(Object.keys(lastSeriesData))
                                    : getFilteredNames(allSeries);
        ensureChartsExist(names);
        if (lastSeriesData) {{
          renderCharts(subsetSeriesData(lastSeriesData, names));
        }}
      }}

      function buildOffsetControls() {{
        const container = document.getElementById('offset-controls');
        container.innerHTML = '';
        sourceLabels.forEach((label, idx) => {{
          const wrap = document.createElement('div');
          wrap.className = 'control-group';
          const labStart = document.createElement('label');
          labStart.htmlFor = `start-${{idx}}`;
          labStart.textContent = `Start ${{idx}}`;
          const inputStart = document.createElement('input');
          inputStart.type = 'number';
          inputStart.step = '1';
          inputStart.id = `start-${{idx}}`;
          inputStart.placeholder = 'min step';

          const labEnd = document.createElement('label');
          labEnd.htmlFor = `end-${{idx}}`;
          labEnd.textContent = `End ${{idx}}`;
          const inputEnd = document.createElement('input');
          inputEnd.type = 'number';
          inputEnd.step = '1';
          inputEnd.id = `end-${{idx}}`;
          inputEnd.placeholder = 'max step';

          const labOff = document.createElement('label');
          labOff.htmlFor = `offset-${{idx}}`;
          labOff.textContent = `Offset ${{idx}}`;
          const inputOff = document.createElement('input');
          inputOff.type = 'number';
          inputOff.step = '1';
          inputOff.id = `offset-${{idx}}`;
          inputOff.value = '0';

          wrap.appendChild(labStart);
          wrap.appendChild(inputStart);
          wrap.appendChild(labEnd);
          wrap.appendChild(inputEnd);
          wrap.appendChild(labOff);
          wrap.appendChild(inputOff);
          container.appendChild(wrap);
        }});
      }}

      function getOffsets() {{
        return sourceLabels.map((_, idx) => {{
          const v = parseInt(document.getElementById(`offset-${{idx}}`).value || '0', 10);
          return Number.isFinite(v) ? v : 0;
        }});
      }}

      function getStarts() {{
        return sourceLabels.map((_, idx) => {{
          const raw = document.getElementById(`start-${{idx}}`).value;
          const v = raw === '' ? null : parseInt(raw, 10);
          return Number.isFinite(v) ? v : null;
        }});
      }}

      function getEnds() {{
        return sourceLabels.map((_, idx) => {{
          const raw = document.getElementById(`end-${{idx}}`).value;
          const v = raw === '' ? null : parseInt(raw, 10);
          return Number.isFinite(v) ? v : null;
        }});
      }}

      function showStatus(text) {{
        document.getElementById('status').textContent = text || '';
      }}

      async function fetchDataWithOffsets(offsets, starts, ends) {{
        const params = new URLSearchParams();
        params.set('o', offsets.join(','));
        // Encode starts/ends as comma-separated, empty for nulls
        params.set('s', starts.map(v => (v === null ? '' : String(v))).join(','));
        params.set('e', ends.map(v => (v === null ? '' : String(v))).join(','));
        const resp = await fetch('/data?' + params.toString());
        if (!resp.ok) throw new Error('Failed to fetch data');
        return await resp.json();
      }}

      function ensureChartsExist(seriesNames) {{
        const charts = document.getElementById('charts');
        charts.innerHTML = '';
        // Group series by category
        const grouped = {{}};
        seriesNames.forEach((name) => {{
          const cat = categoryOf(name);
          if (!grouped[cat]) grouped[cat] = [];
          grouped[cat].push(name);
        }});
        // Render in configured category order first, then any extra categories
        const presentCats = Object.keys(grouped);
        const orderedCats = [
          ...categoriesOrder.filter((c) => presentCats.includes(c)),
          ...presentCats.filter((c) => !categoriesOrder.includes(c)).sort()
        ];
        orderedCats.forEach((cat) => {{
          const section = document.createElement('section');
          section.className = 'category';
          const h = document.createElement('h2');
          h.textContent = cat;
          section.appendChild(h);
          const grid = document.createElement('div');
          grid.className = 'category-grid';
          const names = grouped[cat].slice().sort();
          names.forEach((name) => {{
            const div = document.createElement('div');
            div.className = 'chart';
            const title = document.createElement('h3');
            title.textContent = name;
            const plot = document.createElement('div');
            plot.id = makeId(name);
            plot.style.width = '100%';
            plot.style.height = '320px';
            div.appendChild(title);
            div.appendChild(plot);
            grid.appendChild(div);
          }});
          section.appendChild(grid);
          charts.appendChild(section);
        }});
      }}

      function renderCharts(seriesData) {{
        const colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#e377c2', '#8c564b'];
        const seriesNames = Object.keys(seriesData);
        seriesNames.forEach((name) => {{
          const traces = (seriesData[name] || []).map((src, i) => ({{
            x: src.x || [],
            y: src.y || [],
            mode: 'lines',
            name: sourceLabels[i] || `src ${{i}}`,
            line: {{ color: colors[i % colors.length], width: 1.5 }}
          }}));
          // Apply global source visibility
          traces.forEach((t, i) => {{
            if (!globalSourceEnabled[i]) {{
              t.visible = 'legendonly';
            }}
          }});
          const layout = {{
            margin: {{ l: 40, r: 10, t: 10, b: 30 }},
            showlegend: true,
            legend: {{ orientation: 'h' }},
            xaxis: {{ title: 'step_id + offset' }},
            yaxis: {{ title: name }}
          }};
          Plotly.react(makeId(name), traces, layout, {{ responsive: true }});
        }});
      }}

      function initSourceToggles() {{
        const container = document.getElementById('source-toggles');
        container.querySelectorAll('.src-toggle').forEach((btn) => {{
          btn.addEventListener('click', () => {{
            const idx = parseInt(btn.getAttribute('data-index'), 10);
            if (!Number.isFinite(idx)) return;
            globalSourceEnabled[idx] = !globalSourceEnabled[idx];
            btn.classList.toggle('src-off', !globalSourceEnabled[idx]);
            // Re-render with current data and offsets
            const offsets = getOffsets();
            const starts = getStarts();
            const ends = getEnds();
            fetchDataWithOffsets(offsets, starts, ends).then((data) => {{
              lastSeriesData = data.series_data;
              rerenderWithFilter();
            }});
          }});
        }});
      }}

      async function refresh() {{
        try {{
          showStatus('Loading...');
          const offsets = getOffsets();
          const starts = getStarts();
          const ends = getEnds();
          const data = await fetchDataWithOffsets(offsets, starts, ends);
          lastSeriesData = data.series_data;
          rerenderWithFilter();
          showStatus('');
        }} catch (err) {{
          console.error(err);
          showStatus('Error loading data');
        }}
      }}

      document.getElementById('apply').addEventListener('click', refresh);

      // init
      buildOffsetControls();
      // build chart shells first (filtered if any search typed before fetch)
      rerenderWithFilter();
      // Hook up search box
      document.getElementById('search').addEventListener('input', (e) => {{
        searchQuery = e.target.value || '';
        if (searchDebounceHandle) clearTimeout(searchDebounceHandle);
        searchDebounceHandle = setTimeout(() => {{
          rerenderWithFilter();
        }}, 120);
      }});
      initSourceToggles();
      refresh();
    </script>
  </body>
</html>
    """
    return Response(html, mimetype="text/html")


@app.route("/series")
def series_meta() -> Response:
    return jsonify({
        "sources": SOURCE_LABELS,
        "series": ALL_SERIES,
    })


@app.route("/data")
def data_with_offsets() -> Response:
    # Offsets passed as comma-separated ints, one per source, e.g., o=0,5,-2,0
    raw = request.args.get("o", "")
    try:
        offsets = [int(x.strip()) for x in raw.split(",") if x.strip() != ""]
    except Exception:
        offsets = []
    if len(offsets) < len(DATA_SOURCES):
        offsets = offsets + [0] * (len(DATA_SOURCES) - len(offsets))
    elif len(offsets) > len(DATA_SOURCES):
        offsets = offsets[: len(DATA_SOURCES)]

    # Optional per-source start/end cutoffs (inclusive). Empty string means null.
    raw_s = request.args.get("s", "")
    raw_e = request.args.get("e", "")
    def parse_nullable_int_list(raw_str: str, n: int) -> List[int | None]:
        parts = raw_str.split(",") if raw_str else []
        out: List[int | None] = []
        for i in range(n):
            if i < len(parts):
                token = parts[i].strip()
                if token == "":
                    out.append(None)
                else:
                    try:
                        out.append(int(token))
                    except Exception:
                        out.append(None)
            else:
                out.append(None)
        return out

    starts = parse_nullable_int_list(raw_s, len(DATA_SOURCES))
    ends = parse_nullable_int_list(raw_e, len(DATA_SOURCES))

    # Build response: for each series name, return an array of per-source traces
    series_data: Dict[str, List[Dict[str, List[float]]]] = {}
    for series_name in ALL_SERIES:
        per_source: List[Dict[str, List[float]]] = []
        for src_idx, ds in enumerate(DATA_SOURCES):
            points = ds.series_to_points.get(series_name, [])
            if points:
                # Apply start/end filter first (inclusive)
                s = starts[src_idx]
                e = ends[src_idx]
                if s is not None:
                    points = [p for p in points if p[0] >= s]
                if e is not None:
                    points = [p for p in points if p[0] <= e]
                # Then apply offset to step_id
                x_vals = [step_id + offsets[src_idx] for step_id, _ in points]
                y_vals = [value for _, value in points]
            else:
                x_vals = []
                y_vals = []
            per_source.append({"x": x_vals, "y": y_vals})
        series_data[series_name] = per_source

    return jsonify({
        "sources": SOURCE_LABELS,
        "series_data": series_data,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=True)
