#!/bin/bash
cd "$(dirname "$0")"

if [ -n "$DISCORD_TOKEN" ]; then
  echo "$DISCORD_TOKEN" > tokens.txt
fi

pip install -q -r requirements.txt

python main.py
