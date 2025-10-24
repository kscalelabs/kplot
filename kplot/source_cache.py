"""In-memory cache for data sources with automatic file watching."""

import os
import glob
import json
import threading
from typing import List, Tuple, Optional, Any
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent


class DataSource:
    """Represents a single data source (kinfer log file)."""
    
    def __init__(self, label: str, path: str, mtime: float = 0.0) -> None:
        self.label = label
        self.search_text = label.lower()
        self.path = path
        self.mtime = mtime  # File modification time
        self.series_to_points: dict[str, List[Tuple[int, float]]] = {}
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
    """Check if a value is a number (int or float, but not bool)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def extract_series(record: dict[str, Any], joint_names: List[str]) -> dict[str, float]:
    """Extract all numeric series from a record."""
    series: dict[str, float] = {}
    
    JOINTED_KEYS = {"joint_angles", "joint_velocities", "joint_amps", "joint_torques", 
                    "joint_temps", "output", "action"}
    
    for key, value in record.items():
        if value is None or key == "step_id":
            continue
            
        if is_number(value):
            series[key] = float(value)
        elif isinstance(value, dict):
            # Handle dict values (e.g., command as dict)
            for sub_key, sub_value in value.items():
                if is_number(sub_value):
                    series[f"{key}.{sub_key}"] = float(sub_value)
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


class SourceCacheHandler(FileSystemEventHandler):
    """Handles file system events and triggers cache refresh."""
    
    def __init__(self, cache: 'SourceCache', debug: bool = False) -> None:
        super().__init__()
        self.cache = cache
        self.debug = debug
    
    def _should_trigger(self, path: str, is_directory: bool) -> bool:
        """Check if this path should trigger a rescan."""
        # Trigger on any .ndjson file or directory changes
        if is_directory:
            return True
        if path.endswith('.ndjson'):
            return True
        # Also trigger on parent directories of session folders
        if '/kd-' in path or '/robot' in path:
            return True
        return False
    
    def on_created(self, event: FileSystemEvent) -> None:
        """Called when a file or directory is created."""
        if self._should_trigger(event.src_path, event.is_directory):
            if self.debug:
                print(f"[FileWatcher] Created: {event.src_path}")
            self.cache.schedule_rescan()
    
    def on_deleted(self, event: FileSystemEvent) -> None:
        """Called when a file or directory is deleted."""
        if self._should_trigger(event.src_path, event.is_directory):
            if self.debug:
                print(f"[FileWatcher] Deleted: {event.src_path}")
            self.cache.schedule_rescan()
    
    def on_modified(self, event: FileSystemEvent) -> None:
        """Called when a file is modified."""
        if self._should_trigger(event.src_path, event.is_directory):
            if self.debug:
                print(f"[FileWatcher] Modified: {event.src_path}")
            self.cache.schedule_rescan()
    
    def on_moved(self, event: FileSystemEvent) -> None:
        """Called when a file or directory is moved."""
        if self._should_trigger(event.src_path, event.is_directory):
            if self.debug:
                print(f"[FileWatcher] Moved: {event.src_path}")
            self.cache.schedule_rescan()


class SourceCache:
    """Thread-safe cache for data sources with automatic file watching.
    
    This class maintains an in-memory cache of scanned data sources and
    automatically rescans when the filesystem changes.
    """
    
    def __init__(self, data_dir: str, debug: bool = False) -> None:
        """Initialize the source cache.
        
        Args:
            data_dir: Path to the data directory to watch and scan
            debug: Enable debug logging
        """
        self.data_dir = data_dir
        self.debug = debug
        self._sources: List[DataSource] = []
        self._lock = threading.RLock()
        self._observer: Optional[Observer] = None
        self._rescan_timers: List[threading.Timer] = []  # Multiple pending rescans
        
        # Initial scan
        self._perform_scan()
    
    def _perform_scan(self) -> None:
        """Perform the actual directory scan for data sources."""
        import time
        scan_start = time.time()
        
        if not self.data_dir or not os.path.isdir(self.data_dir):
            with self._lock:
                self._sources = []
                self._rescan_pending = False
            return
        
        discovered: List[Tuple[float, DataSource]] = []
        
        for ndjson_path in glob.glob(
            os.path.join(self.data_dir, "**", "kinfer_log.ndjson"), 
            recursive=True
        ):
            rel_path = os.path.relpath(ndjson_path, self.data_dir)
            parts = rel_path.split(os.sep)
            
            # Expect: robot_name/run_dir/kinfer_log.ndjson
            if len(parts) < 3 or parts[-1] != "kinfer_log.ndjson":
                continue
            
            try:
                filesize = os.path.getsize(ndjson_path)
                if filesize == 0:
                    continue
                
                robot_name, run_dir = parts[0], parts[1]
                label = f"{robot_name} | {run_dir}"
                mtime = os.path.getmtime(ndjson_path)
                
                discovered.append((mtime, DataSource(label, ndjson_path, mtime)))
                
                if self.debug:
                    print(f"[SourceCache] Found: {label} (mtime: {mtime})")
            except OSError as e:
                if self.debug:
                    print(f"[SourceCache] Error accessing {ndjson_path}: {e}")
                continue
        
        # Sort by modification time (newest first)
        discovered.sort(key=lambda item: item[0], reverse=True)
        
        old_count = len(self._sources) if self._sources else 0
        new_sources = [ds for _, ds in discovered]
        
        with self._lock:
            self._sources = new_sources
        
        scan_time = time.time() - scan_start
        if len(new_sources) != old_count or self.debug:
            print(f"[SourceCache] Scanned and found {len(new_sources)} data sources (was {old_count}, took {scan_time:.2f}s)")
            if new_sources and self.debug:
                print(f"[SourceCache] Latest: {new_sources[0].label}")
    
    def schedule_rescan(self) -> None:
        """Schedule 3 rescans at different delays to catch files at different stages.
        
        This handles various timing scenarios:
        - 1s: Catches quickly-written files
        - 10s: Catches slower uploads or rsync operations
        - 60s: Final catch-all for very slow operations
        """
        with self._lock:
            # Cancel any existing pending rescans
            for timer in self._rescan_timers:
                timer.cancel()
            self._rescan_timers.clear()
            
            # Schedule 3 rescans at different times
            delays = [1.0, 10.0, 60.0]
            
            if self.debug:
                print(f"[SourceCache] Scheduling rescans at: {delays} seconds")
            
            for delay in delays:
                timer = threading.Timer(delay, self._perform_scan)
                timer.daemon = True
                timer.start()
                self._rescan_timers.append(timer)
    
    def get_sources(self) -> List[DataSource]:
        """Get the current list of data sources (thread-safe).
        
        Returns:
            List of DataSource objects, sorted by modification time
        """
        with self._lock:
            return self._sources.copy()
    
    def get_source_by_path(self, path: str) -> Optional[DataSource]:
        """Get a data source by its absolute or relative path.
        
        Args:
            path: Absolute path or relative path from data_dir
            
        Returns:
            DataSource object or None if not found
        """
        with self._lock:
            # Try as absolute path first
            for source in self._sources:
                if source.path == path:
                    return source
            
            # Try as relative path from data_dir
            abs_path = os.path.join(self.data_dir, path)
            for source in self._sources:
                if source.path == abs_path:
                    return source
            
            return None
    
    def get_relative_path(self, source: DataSource) -> str:
        """Get the relative path from data_dir for a source.
        
        Args:
            source: DataSource object
            
        Returns:
            Relative path from data_dir
        """
        return os.path.relpath(source.path, self.data_dir)
    
    def start_watching(self) -> None:
        """Start watching the data directory for changes."""
        if self._observer is not None:
            return
        
        self._observer = Observer()
        event_handler = SourceCacheHandler(self, debug=self.debug)
        self._observer.schedule(event_handler, self.data_dir, recursive=True)
        self._observer.start()
        
        print(f"[SourceCache] Started watching {self.data_dir}")
        if self.debug:
            print(f"[SourceCache] Debug mode: rescans at 1s, 10s, 60s after file changes")
    
    def stop_watching(self) -> None:
        """Stop watching the data directory."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
            print("[SourceCache] Stopped watching")
        
        # Cancel all pending rescan timers
        with self._lock:
            for timer in self._rescan_timers:
                timer.cancel()
            self._rescan_timers.clear()
    
    def rescan_now(self) -> None:
        """Force an immediate rescan of the data directory."""
        with self._lock:
            # Cancel all pending rescans
            for timer in self._rescan_timers:
                timer.cancel()
            self._rescan_timers.clear()
        
        self._perform_scan()
    
    def __del__(self) -> None:
        """Cleanup when the cache is destroyed."""
        self.stop_watching()

