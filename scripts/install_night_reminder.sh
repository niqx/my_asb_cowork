#!/bin/bash
# Install d-brain-remind-night systemd timer (requires sudo)
set -e

echo "Installing d-brain-remind-night service and timer..."

sudo tee /etc/systemd/system/d-brain-remind-night.service > /dev/null << 'EOF'
[Unit]
Description=d-brain Night Reminder (food + reflection)
After=network.target

[Service]
Type=oneshot
User=myuser
WorkingDirectory=/home/myuser/projects/my_asb_cowork
ExecStart=/home/myuser/projects/my_asb_cowork/scripts/remind_night.sh
Environment=PYTHONUNBUFFERED=1
EOF

sudo tee /etc/systemd/system/d-brain-remind-night.timer > /dev/null << 'EOF'
[Unit]
Description=Send night reminder daily at 23:00 Moscow

[Timer]
OnCalendar=*-*-* 23:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

# Shift process timer to 23:30 to give 30 min after reminder
sudo sed -i 's/OnCalendar=\*-\*-\* 23:00:00/OnCalendar=*-*-* 23:30:00/' \
    /etc/systemd/system/d-brain-process.timer
sudo sed -i 's/23:00 Moscow (20:00 UTC)/23:30 Moscow (20:30 UTC)/' \
    /etc/systemd/system/d-brain-process.timer

sudo systemctl daemon-reload

sudo systemctl enable d-brain-remind-night.timer
sudo systemctl start d-brain-remind-night.timer

# Restart process timer to pick up new schedule
sudo systemctl restart d-brain-process.timer

echo "Done! Updated schedule:"
systemctl list-timers d-brain-remind-night.timer d-brain-process.timer --no-pager
