import os
import json
import glob
from typing import Dict, List, Tuple, Any, Optional
from flask import Flask, jsonify, request, render_template


app = Flask(__name__)


class DataSource:
    def __init__(self, label: str, path: str) -> None:
        self.label = label
        self.path = path
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


def scan_data_sources(root_dir: str) -> List[DataSource]:
    """Scan for .ndjson files without loading them."""
    data_dir = os.path.join(root_dir, "data") if os.path.isdir(os.path.join(root_dir, "data")) else root_dir
    
    if not os.path.isdir(data_dir):
        return []
    
    ndjson_paths = sorted(set(glob.glob(os.path.join(data_dir, "**", "*.ndjson"), recursive=True)))
    data_sources: List[DataSource] = []

    for path in ndjson_paths:
        try:
            label = os.path.relpath(path, data_dir)
        except Exception:
            label = os.path.basename(path)
        
        data_sources.append(DataSource(label=label, path=path))

    return data_sources


# Global state
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = ROOT_DIR
DATA_SOURCES = scan_data_sources(ROOT_DIR)
SOURCE_LABELS = [ds.label for ds in DATA_SOURCES]


def rescan_sources() -> None:
    """Rescan for new files."""
    global DATA_SOURCES, SOURCE_LABELS
    DATA_SOURCES = scan_data_sources(DATA_DIR)
    SOURCE_LABELS = [ds.label for ds in DATA_SOURCES]


@app.route("/")
def index() -> str:
    return render_template(
        "index.html",
        source_labels=enumerate(SOURCE_LABELS),
        source_labels_json=json.dumps(SOURCE_LABELS),
        all_series_json=json.dumps([]),  # No series until sources are loaded
    )


@app.route("/sources")
def list_sources() -> str:
    """Return list of available data sources."""
    if DATA_DIR != ROOT_DIR and os.path.exists(DATA_DIR):
        rescan_sources()
    return jsonify({"sources": SOURCE_LABELS})


@app.route("/data")
def data() -> str:
    """Return data for selected sources only."""
    # Get selected source indices
    selected_raw = request.args.get("sources", "")
    if not selected_raw:
        return jsonify({"sources": [], "series_data": {}})
    
    try:
        selected_indices = [int(x.strip()) for x in selected_raw.split(",") if x.strip()]
    except Exception:
        return jsonify({"sources": [], "series_data": {}})
    
    # Load only selected sources
    selected_sources = []
    for idx in selected_indices:
        if 0 <= idx < len(DATA_SOURCES):
            ds = DATA_SOURCES[idx]
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
