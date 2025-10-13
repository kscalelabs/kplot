#!/bin/bash
# ONE script to rule them all - sets up kplot server with dedicated venv

set -e

INSTALL_DIR="$HOME/kplot"
VENV_DIR="$INSTALL_DIR/.venv"
DATA_DIR="$HOME/robot_telemetry"
PORT=5001

echo "🚀 Deploying kplot server..."

# Navigate to install directory
cd "$INSTALL_DIR"

# Create venv if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Activate venv and install
echo "📥 Installing kplot..."
source "$VENV_DIR/bin/activate"
pip install --upgrade pip --quiet
pip install -e . --quiet

# Create systemd service file
echo "⚙️  Creating systemd service..."
cat > /tmp/kplot.service << EOF
[Unit]
Description=KPlot Visualization Server
After=network.target

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/kplot-server --data-dir $DATA_DIR --port $PORT
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Install service
sudo cp /tmp/kplot.service /etc/systemd/system/kplot.service
sudo systemctl daemon-reload

# Enable and start/restart
if sudo systemctl is-enabled kplot.service &>/dev/null; then
    echo "🔄 Restarting service..."
    sudo systemctl restart kplot.service
else
    echo "✨ Enabling and starting service..."
    sudo systemctl enable kplot.service
    sudo systemctl start kplot.service
fi

sleep 1

echo ""
echo "✅ Deployment complete!"
echo ""
sudo systemctl status kplot.service --no-pager -l | head -n 15
echo ""
echo "📊 Server running at: http://$(hostname):$PORT"
echo ""
echo "Logs: sudo journalctl -u kplot.service -f"

