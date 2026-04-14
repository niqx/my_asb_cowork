#!/bin/bash
# Install d-brain-weekly systemd timer (requires sudo)
set -e

echo "Installing d-brain-weekly systemd service and timer..."

# 20:00 Moscow = 17:00 UTC
sudo tee /etc/systemd/system/d-brain-weekly.service > /dev/null << 'EOF'
[Unit]
Description=d-brain Weekly Digest & Reflection
After=network.target

[Service]
Type=oneshot
User=myuser
WorkingDirectory=/home/myuser/projects/my_asb_cowork
ExecStart=/home/myuser/projects/my_asb_cowork/scripts/run_weekly.sh
Environment=PYTHONUNBUFFERED=1
EOF

sudo tee /etc/systemd/system/d-brain-weekly.timer > /dev/null << 'EOF'
[Unit]
Description=Run d-brain weekly digest on Sundays at 20:00 Moscow (17:00 UTC)

[Timer]
OnCalendar=Sun *-*-* 17:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable d-brain-weekly.timer
sudo systemctl start d-brain-weekly.timer

# Grant myuser NOPASSWD for d-brain-bot control (needed for cron/systemd context)
sudo tee /etc/sudoers.d/d-brain > /dev/null << 'EOF'
myuser ALL=(ALL) NOPASSWD: /bin/systemctl stop d-brain-bot, /bin/systemctl start d-brain-bot, /usr/bin/timedatectl set-timezone *
EOF
sudo chmod 0440 /etc/sudoers.d/d-brain
echo "Sudoers rule installed: myuser can stop/start d-brain-bot and set timezone without password"

echo "Done! Next run:"
systemctl list-timers d-brain-weekly.timer --no-pager
