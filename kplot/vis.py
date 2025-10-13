import os
import json
import glob
from typing import Dict, List, Tuple, Any
from flask import Flask, jsonify, request, render_template


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
def index() -> str:
    return render_template(
        "index.html",
        source_labels=enumerate(SOURCE_LABELS),
        source_labels_json=json.dumps(SOURCE_LABELS),
        all_series_json=json.dumps(ALL_SERIES),
    )


@app.route("/series")
def series_meta() -> str:
    return jsonify({
        "sources": SOURCE_LABELS,
        "series": ALL_SERIES,
    })


@app.route("/data")
def data_with_offsets() -> str:
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
