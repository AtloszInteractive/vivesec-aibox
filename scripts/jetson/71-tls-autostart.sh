#!/usr/bin/env bash
# Restart the adapter the moment provisioning completes, so its mTLS listener
# picks up the freshly signed certificates without a manual step.
set -u
# The marker appears before the certificates finish landing, so wait for all
# three or the listener comes up without TLS.
while [ ! -f /data/pki/initialized ] || [ ! -s /data/pki/server.crt ] || [ ! -s /data/pki/ca.crt ]; do
  sleep 1
done
sleep 2
docker restart vivesec-adapter
echo "TLS_STARTED_AT $(date -u)"
sleep 4
docker logs --tail 20 vivesec-adapter 2>&1 | grep -i -e TLS -e Provision
