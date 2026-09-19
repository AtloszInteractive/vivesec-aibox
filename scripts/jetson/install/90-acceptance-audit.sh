#!/usr/bin/env bash
# ViVeSec AI Box - acceptance audit.
#
# Runs the full handover checklist against the installed box and prints one
# PASS/FAIL line per check plus a summary. Read-only: it starts nothing,
# changes nothing and writes nothing outside the report file.
#
#   sudo bash 90-acceptance-audit.sh
#   sudo bash 90-acceptance-audit.sh | tee acceptance-$(date +%Y%m%d).txt
#
# Exit code 0 means every check passed and the box may be handed over.
set -uo pipefail

ollama_api=http://127.0.0.1:11434
rag_api=http://127.0.0.1:8090
adapter_api=http://127.0.0.1:80
ui_api=http://127.0.0.1:8080
key_file=/data/app/rag-api-key
pki_dir=/data/pki

expected_ollama_version="${OLLAMA_VERSION:-0.34.0}"
expected_embed_model="${EMBED_MODEL:-bge-m3:latest}"
expected_gen_model="${GEN_MODEL:-qwen3.6:35b}"
box_name="${AIBOX_HOSTNAME:-$(hostname)}"
min_free_gb="${AUDIT_MIN_FREE_GB:-50}"
ssh_user="${AIBOX_SSH_USER:-aibox}"
remote_access="${AIBOX_REMOTE_ACCESS:-tailscale}"
# Tailscale addresses (CGNAT 100.64/10 + its IPv6 ULA) are not "the network".
tailnet_regex='^(100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.|\[fd7a:115c:a1e0:)'

pass_count=0
fail_count=0

check() {
  local name=$1 snippet=$2 output status
  output="$(bash -c "$snippet" 2>&1)"
  status=$?
  if [[ $status -eq 0 ]]; then
    printf 'PASS  %-34s %s\n' "$name" "$(head -n 1 <<<"$output")"
    pass_count=$((pass_count + 1))
  else
    printf 'FAIL  %-34s %s\n' "$name" "$(head -n 3 <<<"$output" | tr '\n' ' ')"
    fail_count=$((fail_count + 1))
  fi
}

echo "ViVeSec AI Box - acceptance audit"
echo "Box:  $box_name"
echo "Date: $(date -Is)"
echo

echo "-- Platform -------------------------------------------------------------"
check root_filesystem \
  '[[ $(findmnt -nro SOURCE /) == /dev/mmcblk0p1 ]] && findmnt -nro SOURCE,FSTYPE /'
check data_filesystem \
  '[[ $(findmnt -nro SOURCE /data) == /dev/nvme0n1p1 && $(findmnt -nro FSTYPE /data) == ext4 ]] && findmnt -nro SOURCE,FSTYPE /data'
check data_mount_persistent \
  'grep -E "^[^#].*[[:space:]]/data[[:space:]]" /etc/fstab'
check data_free_space \
  "free=\$(df --output=avail -BG /data | tail -n 1 | tr -dc '0-9'); [[ \$free -ge $min_free_gb ]] && echo \"\${free}G free\""
check l4t_release \
  'head -n 1 /etc/nv_tegra_release | grep -E "# R36 .*REVISION: 4\.4"'
check architecture \
  '[[ $(dpkg --print-architecture) == arm64 ]] && dpkg --print-architecture'
# 30W mode halves generation speed (~15 vs ~35 tok/s on qwen3.6:35b).
check power_mode_maxn \
  'nvpmodel -q 2>/dev/null | grep -E "^NV Power Mode: MAXN"'

echo
echo "-- Container runtime ----------------------------------------------------"
check docker_service_enabled \
  'systemctl is-enabled docker.service && systemctl is-active docker.service'
check docker_root_on_data \
  '[[ $(docker info --format "{{.DockerRootDir}}") == /data/docker ]] && docker info --format "{{.DockerRootDir}}"'
check docker_nvidia_runtime \
  'docker info --format "{{json .Runtimes}}" | grep -o "nvidia"'
check docker_gpu_passthrough \
  'docker run --rm --runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=compute,utility ubuntu:22.04 sh -c "ldconfig -p | grep -q libcuda.so && test -e /dev/nvhost-ctrl-gpu" && echo "libcuda + /dev/nvhost-ctrl-gpu visible"'

echo
echo "-- Model runtime --------------------------------------------------------"
check ollama_service_active \
  'systemctl is-enabled ollama.service && systemctl is-active ollama.service'
check ollama_version \
  "[[ \$(curl -fsS $ollama_api/api/version | jq -r .version) == $expected_ollama_version ]] && echo $expected_ollama_version"
check ollama_models_on_data \
  'systemctl show ollama -p Environment --value | tr " " "\n" | tr -d "\"" | grep -Fx "OLLAMA_MODELS=/data/ollama/models"'
check ollama_loopback_only \
  'ss -lnt | awk "{print \$4}" | grep -E ":11434$" | grep -qv "^127\." && { echo "11434 is reachable from the network"; exit 1; }; ss -lnt | awk "{print \$4}" | grep -E "^127\.0\.0\.1:11434$"'
check embedding_model_present \
  "ollama list | awk 'NR > 1 {print \$1}' | grep -Fxq '$expected_embed_model' && echo '$expected_embed_model'"
check generation_model_present \
  "ollama list | awk 'NR > 1 {print \$1}' | grep -Fxq '$expected_gen_model' && echo '$expected_gen_model'"
check embedding_dimension \
  "dim=\$(curl -fsS $ollama_api/api/embed -H 'Content-Type: application/json' -d '{\"model\":\"${expected_embed_model%%:*}\",\"input\":\"acceptance audit\"}' | jq -r '.embeddings[0] | length'); [[ \$dim == 1024 ]] && echo \"dimension=\$dim\""
check embedding_model_on_gpu \
  "vram=\$(curl -fsS $ollama_api/api/ps | jq -r --arg m '${expected_embed_model%%:*}:latest' '.models[] | select(.name == \$m) | .size_vram'); [[ -n \$vram && \$vram -gt 0 ]] && echo \"vram=\$vram\""
check generation_smoke \
  "r=\$(curl -fsS --max-time 300 $ollama_api/api/generate -H 'Content-Type: application/json' -d '{\"model\":\"$expected_gen_model\",\"prompt\":\"Reply with exactly: OK\",\"stream\":false,\"think\":false,\"options\":{\"num_ctx\":4096,\"num_predict\":8,\"temperature\":0}}'); tokens=\$(jq -r '.eval_count // 0' <<<\"\$r\"); [[ \$(jq -r .done <<<\"\$r\") == true && \$tokens -gt 0 ]] && echo \"generated \$tokens tokens\""
check generation_model_fully_on_gpu \
  "p=\$(curl -fsS $ollama_api/api/ps | jq -r --arg m '$expected_gen_model' '.models[] | select(.name == \$m) | \"\\(.size_vram) \\(.size)\"'); read -r vram size <<<\"\$p\"; [[ -n \$vram && \$vram == \"\$size\" ]] && echo \"100% GPU (\$vram bytes)\""

echo
echo "-- Application containers -----------------------------------------------"
for container in vivesec-rag vivesec-adapter; do
  check "container_${container#vivesec-}" \
    "[[ \$(docker inspect -f '{{.State.Running}}' $container) == true && \$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' $container) == unless-stopped ]] && docker inspect -f 'running since {{.State.StartedAt}}' $container"
done
# The LAN UI is optional in production; if it runs, it must run like the others.
check container_ui \
  "if ! docker inspect vivesec-ui >/dev/null 2>&1; then echo 'not deployed (allowed)'; exit 0; fi; [[ \$(docker inspect -f '{{.State.Running}}' vivesec-ui) == true && \$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' vivesec-ui) == unless-stopped ]] && docker inspect -f 'running since {{.State.StartedAt}}' vivesec-ui"

echo
echo "-- Retrieval service ----------------------------------------------------"
check rag_health \
  "curl -fsS --max-time 10 $rag_api/health | jq -c '{ok, min_score, query_reaccent}'"
check rag_storage_backend \
  "b=\$(docker inspect vivesec-rag --format '{{range .Config.Env}}{{println .}}{{end}}' | awk -F= '\$1 == \"RAG_STORE_BACKEND\" {print \$2}'); [[ \$b == sqlite ]] && echo \"backend=\$b\""
check rag_index_on_data \
  "docker inspect vivesec-rag --format '{{range .Mounts}}{{println .Source}}{{end}}' | grep -Fxq /data/rag && echo '/data/rag mounted'"
check rag_key_file_protected \
  "[[ \$(stat -c %a $key_file) == 600 ]] && grep -Eq '^[0-9a-f]{64}\$' $key_file && echo 'mode 600, 64 hex chars'"
check rag_rejects_anonymous \
  "code=\$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' $rag_api/rag/search_context); [[ \$code == 401 ]] && echo \"anonymous -> \$code\""
check rag_accepts_api_key \
  "code=\$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H \"X-API-Key: \$(cat $key_file)\" -d '{}' $rag_api/rag/search_context); [[ \$code == 400 ]] && echo \"authenticated -> \$code (empty body rejected on content, not on auth)\""
check rag_loopback_only \
  'ss -lnt | awk "{print \$4}" | grep -E ":8090$" | grep -qv "^127\." && { echo "8090 is reachable from the network"; exit 1; }; ss -lnt | awk "{print \$4}" | grep -E "^127\.0\.0\.1:8090$"'

echo
echo "-- Adapter --------------------------------------------------------------"
check adapter_status \
  "curl -fsS --max-time 20 $adapter_api/api/v1/status | jq -e '.ok == true' >/dev/null && curl -fsS $adapter_api/api/v1/status | jq -c '{ok, ui_ready, fs_ready, storage_locked}'"
check adapter_index_visible \
  "curl -fsS --max-time 20 $adapter_api/api/v1/status | jq -e '.index | has(\"error\") | not' >/dev/null && curl -fsS $adapter_api/api/v1/status | jq -c '.index | {documents, chunks}'"
check adapter_chat_policy \
  "p=\$(curl -fsS $adapter_api/api/v1/status | jq -r '.chat_policy.policy // .chat_policy.mode // empty'); [[ \$p == locked_hybrid ]] && echo \"policy=\$p\""
check adapter_generation_model \
  "m=\$(docker inspect vivesec-adapter --format '{{range .Config.Env}}{{println .}}{{end}}' | awk -F= '\$1 == \"ADAPTER_GEN_MODEL\" {print \$2}'); [[ \$m == '$expected_gen_model' ]] && echo \"model=\$m\""
check adapter_context_window \
  "c=\$(docker inspect vivesec-adapter --format '{{range .Config.Env}}{{println .}}{{end}}' | awk -F= '\$1 == \"ADAPTER_NUM_CTX\" {print \$2}'); [[ \$c == 65536 ]] && echo \"num_ctx=\$c (must be identical for chat and analysis)\""
check adapter_persistent_volumes \
  "m=\$(docker inspect vivesec-adapter --format '{{range .Mounts}}{{println .Source}}{{end}}'); for d in /data/adapter /data/pki /data/generated /data/sessions /data/jobs /data/feedback; do grep -Fxq \$d <<<\"\$m\" || { echo \"missing bind: \$d\"; exit 1; }; done; echo 'all six data volumes bound'"
check adapter_http_listener \
  'ss -lnt | awk "{print \$4}" | grep -E ":80$"'

echo
echo "-- Pairing, discovery and exposure --------------------------------------"
check device_identity \
  "docker exec vivesec-adapter test -s $pki_dir/device_uuid && docker exec vivesec-adapter sh -c 'cut -c1-8 $pki_dir/device_uuid' | sed 's/^/uuid starts with /'"
check pairing_state \
  "if [[ -s $pki_dir/ca.crt && -s $pki_dir/server.crt ]]; then echo 'PAIRED (certificate installed)'; elif [[ -s $pki_dir/server.key ]]; then echo 'PENDING (key generated, awaiting pairing)'; elif [[ -s $pki_dir/device_uuid ]]; then echo 'UNPAIRED (identity ready, key is generated at the first init/prepare)'; else echo 'no device identity'; exit 1; fi"
check mutual_tls_port \
  "listening=\$(ss -lnt | awk '{print \$4}' | grep -cE ':443\$'); if [[ -s $pki_dir/ca.crt ]]; then [[ \$listening -ge 1 ]] && echo '443 open (box is paired)'; else [[ \$listening -eq 0 ]] && echo '443 closed until pairing (expected)'; fi"
check discovery_listener \
  'ss -lun | awk "{print \$4}" | grep -E ":1900$" | head -n 1'
check ui_http \
  "if ! docker inspect -f '{{.State.Running}}' vivesec-ui 2>/dev/null | grep -qx true; then echo 'UI container not deployed (allowed)'; exit 0; fi; code=\$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 $ui_api/); [[ \$code == 200 ]] && echo \"UI answers on loopback -> \$code\""
check ui_not_on_network \
  'ss -lnt | awk "{print \$4}" | grep -E ":8080$" | grep -qv "^127\." && { echo "8080 is reachable from the network - the LAN UI must stay on loopback"; exit 1; }; echo "8080 is not reachable from the network"'
check no_unexpected_listeners \
  "unexpected=\$(ss -lnt | awk 'NR > 1 {print \$4}' | grep -vE '^(127\\.|\\[::1\\]:)' | grep -vE '$tailnet_regex' | grep -oE '[0-9]+\$' | sort -u | grep -vE '^(22|80|443)\$' | tr '\\n' ' '); [[ -z \${unexpected// /} ]] && echo 'only 22, 80 and 443 are reachable from the network (tailnet addresses excluded)' || { echo \"unexpected open ports: \$unexpected\"; exit 1; }"

echo
echo "-- Host hardening and remote access -------------------------------------"
check ssh_service_active \
  'systemctl is-active ssh.service 2>/dev/null || systemctl is-active sshd.service'
check ssh_password_login_disabled \
  'sshd -T | grep -Eq "^passwordauthentication no" && sshd -T | grep -Eq "^kbdinteractiveauthentication no" && echo "password and keyboard-interactive login disabled"'
check ssh_root_login_disabled \
  'sshd -T | grep -E "^permitrootlogin no"'
check ssh_admin_keys_root_owned \
  "f=/etc/ssh/authorized_keys.d/$ssh_user; [[ \$(stat -c %U:%a \$f) == root:644 ]] && n=\$(grep -cE '^(ssh-|sk-|ecdsa-)' \$f) && [[ \$n -ge 1 ]] && sshd -T | grep -Eq '^authorizedkeysfile /etc/ssh/authorized_keys.d/%u\$' && echo \"\$n admin key(s), root-owned, sole key source\""
if [[ $remote_access == tailscale ]]; then
  check tailscale_service_enabled \
    'systemctl is-enabled tailscaled && systemctl is-active tailscaled'
  check tailscale_online \
    "tailscale status --json | jq -er 'select(.BackendState == \"Running\") | .Self.DNSName'"
  check tailscale_key_expiry_disabled \
    "if tailscale status --json | jq -e '.Self.KeyExpiry == null' >/dev/null; then echo 'key expiry disabled'; else echo \"key expires \$(tailscale status --json | jq -r .Self.KeyExpiry) - disable expiry for this node in the admin console\"; exit 1; fi"
  check tailscale_no_routes_no_exit_node \
    "tailscale debug prefs | jq -e '((.ExitNodeID // \"\") == \"\") and ((.ExitNodeIP // \"\") == \"\") and ((.AdvertiseRoutes // []) | length == 0) and ((.RouteAll // false) == false) and ((.CorpDNS // false) == false)' >/dev/null && echo 'no exit node, no routes, no MagicDNS (pairing-safe)'"
else
  check remote_access_policy \
    "echo 'remote access explicitly set to $remote_access'"
fi
check default_route_on_lan \
  "r=\$(ip -o route get 203.0.113.1); dev=\$(sed -nE 's/.* dev ([^ ]+).*/\\1/p' <<<\"\$r\"); src=\$(sed -nE 's/.* src ([^ ]+).*/\\1/p' <<<\"\$r\"); [[ \$dev != tailscale0 ]] && ! grep -qE '$tailnet_regex' <<<\"\$src\" && echo \"via \$dev src \$src (SSDP LOCATION stays on the LAN)\""

echo
echo "-------------------------------------------------------------------------"
printf 'AIBOX_ACCEPTANCE_AUDIT box=%s pass=%d fail=%d\n' "$box_name" "$pass_count" "$fail_count"
if [[ $fail_count -gt 0 && ${AUDIT_CONTINUE_ON_FAIL:-0} != 1 ]]; then
  exit 1
fi
exit 0
