#!/bin/bash
cd "$(dirname "$0")"

export DISCORD_BOT_TOKEN="${DISCORD_BOT_TOKEN}"

python main.py
