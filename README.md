# kplot

Web-based visualization for kinfer-log robot telemetry data.
<img width="2144" height="1905" alt="image" src="https://github.com/user-attachments/assets/5365491f-99a7-4fc2-a36b-db3422dcbb5e" />
DATA DATA DATA DATA!!!!

## Server Setup (Production)

Deploy server with systemd:

```bash
git clone https://github.com/kscalelabs/kplot.git ~/kplot
cd ~/kplot
./scripts/deploy.sh
```

Server runs at `http://your-server:5001`

Updates:
```bash
cd ~/kplot && git pull && ./scripts/deploy.sh
```

Logs:
```bash
sudo journalctl -u kplot.service -f
```

## Local Development

```bash
cd ~/kplot
pip install -e .
kplot-server --data-dir ~/robot_telemetry --port 5001
```

Open: `http://localhost:5001`

### Pages

- **`/`** - Main viewer with source selection sidebar
- **`/latest`** - Auto-updating view of the most recent run (refreshes every 2 seconds)

## Data Format

```
~/robot_telemetry/
├── kd-1/
│   └── session_20251013_110106/
│       └── kinfer_log.ndjson
├── kd-8/
│   └── session_20251013_120000/
│       └── kinfer_log.ndjson
```

NDJSON records:
```json
{
  "step_id": 0,
  "timestamp": 1760378501.967,
  "joint_angles": [...],
  "joint_velocities": [...],
  "joint_order": ["dof_left_hip_pitch_04", ...]
}
```
