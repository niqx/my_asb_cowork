#!/bin/bash
# Install d-brain-daytime-check systemd timer (requires sudo)
# Runs at: 08:00, 10:00, 13:00, 17:00 UTC
# = 11:00, 13:00, 16:00, 20:00 MSK
set -e

echo "Installing d-brain-daytime-check service and timer..."

sudo tee /etc/systemd/system/d-brain-daytime-check.service > /dev/null << 'EOF'
[Unit]
Description=d-brain Daytime Check (every 2 hours)
After=network.target

[Service]
Type=oneshot
User=myuser
WorkingDirectory=/home/myuser/projects/my_asb_cowork
ExecStart=/home/myuser/projects/my_asb_cowork/scripts/daytime_check.sh
Environment=PYTHONUNBUFFERED=1
EOF

sudo tee /etc/systemd/system/d-brain-daytime-check.timer > /dev/null << 'EOF'
[Unit]
Description=Run d-brain daytime check at 11:00, 13:00, 16:00, 20:00 MSK

[Timer]
OnCalendar=*-*-* 08,10,13,17:00:00
Persistent=false

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable d-brain-daytime-check.timer
sudo systemctl start d-brain-daytime-check.timer

echo "Done! Next runs:"
systemctl list-timers d-brain-daytime-check.timer --no-pager
