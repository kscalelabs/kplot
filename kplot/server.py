#!/usr/bin/env python3
"""Simple server launcher for kplot visualization."""

import argparse
import sys
from pathlib import Path
from kplot import vis


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Launch kplot server to visualize kinfer logs",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-dir", 
        type=str,
        default="~/robot_telemetry",
        help="Path to robot telemetry data directory"
    )
    parser.add_argument(
        "--port",
        type=int, 
        default=5001,
        help="Port to run the server on"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0", 
        help="Host to bind to"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run in debug mode (enables Flask debug + file watcher debug logging)"
    )
    parser.add_argument(
        "--watch-debug",
        action="store_true",
        help="Enable debug logging for file watcher only"
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser().resolve()
    if not data_dir.exists():
        print(f"Error: Data directory does not exist: {data_dir}", file=sys.stderr)
        sys.exit(1)

    # Initialize the source cache with file watching
    watch_debug = args.debug or args.watch_debug
    cache = vis.init_cache(str(data_dir), debug=watch_debug)
    sources = cache.get_sources()

    print(f"Loading data from: {data_dir}")
    print(f"Found {len(sources)} data sources")
    print("Data will be loaded on-demand when sources are selected")
    print("File watcher is active - new files will be detected automatically")
    print(f"\nStarting server at http://{args.host}:{args.port}")
    print(f"  Main view:   http://{args.host}:{args.port}/")
    print(f"  Latest view: http://{args.host}:{args.port}/latest")
    print("Press Ctrl+C to stop")

    try:
        vis.app.run(host=args.host, port=args.port, debug=args.debug)
    finally:
        # Clean up the file watcher on shutdown
        cache.stop_watching()


if __name__ == "__main__":
    main()
