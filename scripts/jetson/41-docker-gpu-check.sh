#!/usr/bin/env bash
# Quick proof that the NVIDIA container runtime exposes the Jetson GPU to a plain
# container (CSV mode mounts Tegra device nodes + CUDA libs). No NGC image needed.
set -euo pipefail
docker run --rm ubuntu bash -c '
  echo "===DEVS===";
  ls -1 /dev/nv* 2>/dev/null || echo "(none)";
  echo "===TEGRA-LIBS===";
  ls -1 /usr/lib/aarch64-linux-gnu/tegra/ 2>/dev/null | grep -iE "cuda|nvrm|gpu|nvdla" | head || echo "(none)";
  echo "===CUDA-RT-SO===";
  find / -name "libcuda.so*" 2>/dev/null | head;
'
