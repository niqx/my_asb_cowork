#!/bin/bash
set -e
source "$(dirname "$0")/common.sh"
init

MESSAGE="☀️ <b>Как прошло утро?</b>
Запиши пару строк — что успел, какие были задачи или мысли. Это попадёт в вечерний отчёт."

send_telegram "$MESSAGE"
echo "Morning reminder sent at $(date)"
