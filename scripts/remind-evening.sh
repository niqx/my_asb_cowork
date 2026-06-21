#!/bin/bash
set -e
source "$(dirname "$0")/common.sh"
init

MESSAGE="🌆 <b>Как прошёл день?</b>
Запиши, что сделал, какие были события или мысли — пригодится для вечернего итога."

send_telegram "$MESSAGE"
echo "Day reminder sent at $(date)"
