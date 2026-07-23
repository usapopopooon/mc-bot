#!/bin/sh
set -eu

chown -R mc-bot:mc-bot /data
exec setpriv --reuid=mc-bot --regid=mc-bot --init-groups mc-bot
