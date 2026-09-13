#!/usr/bin/env bash
set -euo pipefail

[[ $(findmnt -nro SOURCE /) == /dev/mmcblk0p1 ]]
[[ $(findmnt -nro SOURCE /data) == /dev/nvme0n1p1 ]]
[[ $(docker info --format '{{.DockerRootDir}}') == /data/docker ]]
docker info --format '{{json .Runtimes}}' | grep -q 'nvidia'

docker version
docker compose version
docker run --rm --runtime=nvidia \
  -e NVIDIA_VISIBLE_DEVICES=all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
  ubuntu:22.04 sh -c \
  'ldconfig -p | grep -q libcuda.so && test -e /dev/nvhost-ctrl-gpu'

echo "JP6_DOCKER_GPU_OK"