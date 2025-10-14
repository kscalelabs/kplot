#!/usr/bin/env python3
"""Simple server launcher for kplot visualization."""

import argparse
import os
import sys
from pathlib import Path

from kplot.vis import app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch kplot server to visualize kinfer logs",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="~/robot_telemetry",
        help="Path to robot telemetry data directory",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5001,
        help="Port to run the server on",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind to",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run in debug mode",
    )

    args = parser.parse_args()

    # Expand user path and resolve
    data_dir = Path(args.data_dir).expanduser().resolve()

    if not data_dir.exists():
        print(f"Error: Data directory does not exist: {data_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading data from: {data_dir}")

    # Scan for data sources from the specified directory
    from kplot import vis

    vis.DATA_DIR = str(data_dir)  # Set the data directory for rescan
    vis.DATA_SOURCES = vis.scan_data_sources(str(data_dir))
    vis.SOURCE_LABELS = [ds.label for ds in vis.DATA_SOURCES]

    print(f"Found {len(vis.DATA_SOURCES)} data sources")
    print("Data will be loaded on-demand when sources are selected")
    print(f"\nStarting server at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()

