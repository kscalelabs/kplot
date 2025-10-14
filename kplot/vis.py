import os
import json
import glob
from typing import Dict, List, Tuple, Any
from flask import Flask, jsonify, request, render_template


app = Flask(__name__)


# Data directory set by server.py
DATA_DIR = ""


def scan_sources() -> List['DataSource']:
    """Scan DATA_DIR for kinfer_log.ndjson files and return DataSource list."""
    if not DATA_DIR or not os.path.isdir(DATA_DIR):
        return []
    
    # Group sources by robot name
    robot_sources: Dict[str, List[Tuple[str, str, float, str]]] = {}
    
    for path in glob.glob(os.path.join(DATA_DIR, "**", "kinfer_log.ndjson"), recursive=True):
        rel_path = os.path.relpath(path, DATA_DIR)
        parts = rel_path.split(os.sep)
        
        # Structure: robot_name/run_dir/kinfer_log.ndjson
        if len(parts) >= 3 and parts[-1] == "kinfer_log.ndjson":
            robot_name = parts[0]
            run_dir = parts[1]
            
            # Get directory modification time
            dir_path = os.path.join(DATA_DIR, robot_name, run_dir)
            try:
                mtime = os.path.getmtime(dir_path)
            except:
                mtime = 0
            
            search_text = f"{robot_name} {run_dir}".lower()
            
            if robot_name not in robot_sources:
                robot_sources[robot_name] = []
            robot_sources[robot_name].append((run_dir, search_text, mtime, path))
    
    # Build final source list: sorted by robot, then by modification time
    sources = []
    for robot_name in sorted(robot_sources.keys()):
        runs = robot_sources[robot_name]
        # Sort by modification time (earliest first)
        runs.sort(key=lambda x: x[2])
        
        for run_dir, search_text, mtime, path in runs:
            label = f"{robot_name} | {run_dir}"
            sources.append(DataSource(label, path, search_text))
    
    return sources


class DataSource:
    def __init__(self, label: str, path: str, search_text: str = "") -> None:
        self.label = label
        self.path = path
        self.search_text = search_text or label.lower()
        self.series_to_points: Dict[str, List[Tuple[int, float]]] = {}
        self.loaded = False

    def load(self) -> None:
        """Load data from file on-demand."""
        if self.loaded:
            return
        
        try:
            with open(self.path, "r", encoding="utf-8") as f:
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
                    
                    if isinstance(record.get("joint_order"), list):
                        joint_names = [str(x) for x in record["joint_order"]]
                    
                    step_id = record.get("step_id")
                    if step_id is None:
                        continue
                    if not isinstance(step_id, int):
                        try:
                            step_id = int(step_id)
                        except Exception:
                            continue
                    
                    for name, value in extract_series(record, joint_names).items():
                        if name not in self.series_to_points:
                            self.series_to_points[name] = []
                        self.series_to_points[name].append((step_id, value))
            
            # Sort points
            for points in self.series_to_points.values():
                points.sort(key=lambda p: p[0])
            
            self.loaded = True
        except Exception as e:
            print(f"Error loading {self.path}: {e}")


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def extract_series(record: Dict[str, Any], joint_names: List[str]) -> Dict[str, float]:
    """Extract all numeric series from a record."""
    series: Dict[str, float] = {}
    
    JOINTED_KEYS = {"joint_angles", "joint_velocities", "joint_amps", "joint_torques", 
                    "joint_temps", "output", "action", "torque_commands", "torque_diff"}
    
    for key, value in record.items():
        if value is None or key == "step_id":
            continue
            
        if is_number(value):
            series[key] = float(value)
        elif isinstance(value, list):
            if key in JOINTED_KEYS:
                for idx, v in enumerate(value):
                    if is_number(v):
                        joint_name = joint_names[idx] if idx < len(joint_names) else f"idx{idx}"
                        series[f"{key}.{joint_name}"] = float(v)
            else:
                for idx, v in enumerate(value):
                    if is_number(v):
                        series[f"{key}[{idx}]"] = float(v)
    
    return series


@app.route("/")
def index() -> str:
    sources = scan_sources()
    labels = [s.label for s in sources]
    search_texts = [s.search_text for s in sources]
    return render_template(
        "index.html",
        source_labels=enumerate(labels),
        source_labels_json=json.dumps(labels),
        search_texts_json=json.dumps(search_texts),
        all_series_json=json.dumps([]),
    )


@app.route("/sources")
def list_sources() -> str:
    """Return list of available data sources."""
    sources = scan_sources()
    return jsonify({
        "sources": [s.label for s in sources],
        "search_texts": [s.search_text for s in sources]
    })


@app.route("/data")
def data() -> str:
    """Return data for selected sources only."""
    selected_raw = request.args.get("sources", "")
    if not selected_raw:
        return jsonify({"sources": [], "series_data": {}})
    
    try:
        selected_indices = [int(x.strip()) for x in selected_raw.split(",") if x.strip()]
    except Exception:
        return jsonify({"sources": [], "series_data": {}})
    
    # Scan and get selected sources
    all_sources = scan_sources()
    selected_sources = []
    for idx in selected_indices:
        if 0 <= idx < len(all_sources):
            ds = all_sources[idx]
            ds.load()
            selected_sources.append(ds)
    
    if not selected_sources:
        return jsonify({"sources": [], "series_data": {}})
    
    # Get all series from loaded sources
    all_series = sorted({name for ds in selected_sources for name in ds.series_to_points.keys()})
    
    # Parse offsets
    raw = request.args.get("o", "")
    offsets = [0] * len(selected_sources)
    if raw:
        try:
            parsed = [int(x.strip()) for x in raw.split(",") if x.strip()]
            offsets = parsed[:len(selected_sources)] + [0] * max(0, len(selected_sources) - len(parsed))
        except Exception:
            pass

    # Build response
    series_data: Dict[str, List[Dict[str, List[float]]]] = {}
    for series_name in all_series:
        per_source = []
        for idx, ds in enumerate(selected_sources):
            points = ds.series_to_points.get(series_name, [])
            if points:
                x = [step_id + offsets[idx] for step_id, _ in points]
                y = [value for _, value in points]
            else:
                x, y = [], []
            per_source.append({"x": x, "y": y})
        series_data[series_name] = per_source

    return jsonify({
        "sources": [ds.label for ds in selected_sources],
        "series": all_series,
        "series_data": series_data
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=True)
