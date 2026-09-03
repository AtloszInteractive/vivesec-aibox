#!/usr/bin/env bash
# Verify the tailnet HTTPS front door is alive and Funnel is off.
set -u
echo -n "tailnet HTTPS : "
curl -s -o /dev/null -w '%{http_code}\n' https://vivesec-aibox.taila8586f.ts.net/
echo -n "local UI      : "
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/
echo
echo "serve config:"
tailscale serve status
