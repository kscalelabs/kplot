import os
import json
from typing import Dict, List, Tuple, Any, Optional
from flask import Flask, jsonify, request, render_template
from flask_compress import Compress
from .source_cache import SourceCache, DataSource


app = Flask(__name__)
Compress(app)  # Enable automatic gzip compression for all responses


# Global source cache (initialized by server.py)
_source_cache: Optional[SourceCache] = None


def init_cache(data_dir: str, debug: bool = False) -> SourceCache:
    """Initialize and start the source cache with file watching.
    
    Args:
        data_dir: Path to the data directory to watch
        debug: Enable debug logging for file watching
        
    Returns:
        The initialized SourceCache instance
    """
    global _source_cache
    _source_cache = SourceCache(data_dir, debug=debug)
    _source_cache.start_watching()
    return _source_cache


def get_cache() -> Optional[SourceCache]:
    """Get the current source cache instance.
    
    Returns:
        The SourceCache instance or None if not initialized
    """
    return _source_cache


def scan_sources() -> List[DataSource]:
    """Returns list of data sources from the cache.
    
    Uses the in-memory cache instead of scanning the disk each time.
    """
    if _source_cache is None:
        return []
    return _source_cache.get_sources()


@app.route("/")
def index() -> str:
    sources = scan_sources()
    labels = [s.label for s in sources]
    search_texts = [s.search_text for s in sources]
    # Get relative paths for stable identifiers
    cache = get_cache()
    paths = [cache.get_relative_path(s) if cache else "" for s in sources]
    return render_template(
        "index.html",
        source_labels=enumerate(labels),
        source_labels_json=json.dumps(labels),
        search_texts_json=json.dumps(search_texts),
        source_paths_json=json.dumps(paths),
        all_series_json=json.dumps([]),
    )


@app.route("/latest")
def latest() -> str:
    """Render the latest run viewer page."""
    sources = scan_sources()
    cache = get_cache()
    if sources and cache:
        latest_label = sources[0].label
        latest_path = cache.get_relative_path(sources[0])
    else:
        latest_label = None
        latest_path = None
    
    return render_template(
        "latest.html",
        latest_label=latest_label,
        latest_path=latest_path,
    )


@app.route("/latest-info")
def latest_info() -> str:
    """Return info about the current latest source (for auto-refresh polling)."""
    sources = scan_sources()
    cache = get_cache()
    if sources and cache:
        return jsonify({
            "label": sources[0].label,
            "path": cache.get_relative_path(sources[0]),
            "mtime": sources[0].mtime,
        })
    else:
        return jsonify({
            "label": None,
            "path": None,
            "mtime": None,
        })


@app.route("/sources")
def list_sources() -> str:
    """Return list of available data sources."""
    sources = scan_sources()
    cache = get_cache()
    return jsonify({
        "sources": [s.label for s in sources],
        "search_texts": [s.search_text for s in sources],
        "paths": [cache.get_relative_path(s) if cache else "" for s in sources]
    })


@app.route("/data")
def data() -> str:
    """Return series data for user-selected sources.

    Query params:
      - sources: comma-separated paths or indices into scan_sources() ordering
      - o: optional comma-separated integer offsets per selected source
    """
    selected_raw = request.args.get("sources", "")
    if not selected_raw:
        return jsonify({"sources": [], "series_data": {}})

    selected_identifiers = [x.strip() for x in selected_raw.split(",") if x.strip()]
    if not selected_identifiers:
        return jsonify({"sources": [], "series_data": {}})

    all_sources = scan_sources()
    cache = get_cache()
    selected_sources: List[DataSource] = []
    
    for identifier in selected_identifiers:
        ds = None
        
        # Try as numeric index first for backward compatibility
        try:
            idx = int(identifier)
            if 0 <= idx < len(all_sources):
                ds = all_sources[idx]
        except ValueError:
            # Not a number, try as path
            if cache:
                ds = cache.get_source_by_path(identifier)
        
        if ds:
            ds.load()
            selected_sources.append(ds)

    if not selected_sources:
        return jsonify({"sources": [], "series_data": {}})

    # Parse optional offsets, padded/truncated to match number of sources
    raw_offsets = request.args.get("o", "")
    try:
        parsed_offsets = [int(x.strip()) for x in raw_offsets.split(",") if x.strip()]
    except Exception:
        parsed_offsets = []
    offsets = (parsed_offsets + [0] * len(selected_sources))[:len(selected_sources)]

    # Union of series across selected sources
    all_series = sorted({name for ds in selected_sources for name in ds.series_to_points})

    # Build series -> per-source {x,y} arrays
    series_data: Dict[str, List[Dict[str, List[float]]]] = {
        series_name: [
            {
                "x": [step_id + offsets[src_idx] for step_id, _ in selected_sources[src_idx].series_to_points.get(series_name, [])],
                "y": [value for _, value in selected_sources[src_idx].series_to_points.get(series_name, [])],
            }
            for src_idx in range(len(selected_sources))
        ]
        for series_name in all_series
    }

    return jsonify({
        "sources": [ds.label for ds in selected_sources],
        "series": all_series,
        "series_data": series_data,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=True)
