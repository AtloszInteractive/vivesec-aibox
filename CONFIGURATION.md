# Configuration register

<!-- Generated from scripts/release/config_registry.json by
     `python scripts/release/config_registry.py render`. Do not edit by hand. -->

Every deployment parameter of the AI Box. **Default** is the value the code uses
when the variable is unset; **Image** is the value baked into the container image
(empty cell: not set by the image). **Change** says what a change requires:
`install` - the installation/redeploy procedure, `restart` - recreating the
container with the new environment, `reindex` - additionally rebuilding the index.
Parameters marked *dev only* must not be set on a production box. Secret values
are never recorded in manifests or logs.

Check a running box: `python scripts/release/config_registry.py validate env.txt`.

## Adapter

| Parameter | Default | Image | Allowed | Change | Impact | Invalid value |
| --- | --- | --- | --- | --- | --- | --- |
| `ADAPTER_ACL` | `enforce` |  | enforce \| off | restart | Document-level access control (E03): filtering of retrieval, file names, folder listing, download and the per-user system view. | Unset or empty is enforce; unknown values fall back to enforce with a warning. off is for diagnostics only: it disables file, folder and room ACLs and classification (the drive scope still applies). |
| `ADAPTER_ACL_DEFAULT_CLASSIFICATION` | `internal` |  | public \| internal \| confidential \| strictly_confidential \| personal (Hungarian aliases accepted) | restart | Classification of a document whose path carries none. | Unknown values fall back to personal (the highest level) with a warning, which hides documents from users without the matching clearance. |
| `ADAPTER_ACL_DEFAULT_CLEARANCE` | `internal` |  | public \| internal \| confidential \| strictly_confidential \| personal (Hungarian aliases accepted) | restart | Clearance of a user who has no mapping in the rules file; a user sees documents up to this level. | Unknown values fall back to public (the lowest level) with a warning. |
| `ADAPTER_ACL_RULES` | *unset* |  | path of a JSON rules file (paths, clearance, default_classification) | restart | Administrator ACLs for rooms (drive roots) and folders, document classification and user clearance by user, group or role. Re-read on change; the RAG copy is resynced automatically. | Empty means no administrator rules. A configured file that is missing, unreadable or malformed denies every document until it is fixed; a malformed single rule denies its subtree. |
| `ADAPTER_AGENT` | `operations` |  | operations; unknown accepted | restart | Default persona id when a request omits agent. | Unknown ids fall back to the generic assistant persona. |
| `ADAPTER_ANALYZE_MAX_CHARS` | `100000` | `100000` | positive chars (not enforced) | restart | Max characters fed to whole-document #analyze. | Unset uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_ANALYZE_MAX_CONTEXT_TOKENS` | `60000` | `60000` | positive token budget (not enforced) | restart | Word-estimated context budget for #analyze. | Unset uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_ANALYZE_MIN_ANSWER_CHARS` | `120` |  | non-negative chars (not enforced) | restart | Minimum length for structured analysis before treating generation as starved. | Unset uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_ANALYZE_NUM_CTX` | `65536` | `65536` | 0 or positive Ollama num_ctx (not enforced) | restart | Ollama context window for #analyze; 0 omits the option. | Unset or empty uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_ANSWER_LANG` | *unset* | *empty* | language name or empty | restart | Forces adapter answers to a language when non-empty. | Any string is accepted at startup; bad values fail only in the consumer if at all. |
| `ADAPTER_CHAT` | `on` |  | off disables; any other value enables | restart | Enables follow-up condensation and chat-history answers. | Only literal off disables chat; all other values enable it. |
| `ADAPTER_CHAT_POLICY` | `locked_hybrid` | `locked_hybrid` | locked_grounded \| locked_hybrid \| selectable_grounded \| selectable_hybrid | restart | Controls default grounded/hybrid chat profile and whether users may switch. | Unknown falls back to locked_grounded. |
| `ADAPTER_CONDENSE_TIMEOUT` | `60` |  | positive seconds (not enforced) | restart | Timeout for the follow-up condensation model call. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_CONF_ADHERENCE_MINOR` | `0.2` |  | 0.0-1.0 ratio (not enforced) | restart | Minor numeric mismatch ratio tolerated by confidence scoring. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_CONF_ADHERENCE_MIN_NUMBERS` | `6` |  | non-negative count (not enforced) | restart | Numeric-count threshold for confidence adherence scoring. | Unset or empty uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_CONF_SIM_HIGH` | `0.65` |  | similarity threshold, usually 0.0-1.0 (not enforced) | restart | High similarity threshold for confidence scoring. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_CONF_SIM_LOW` | `0.45` |  | similarity threshold, usually 0.0-1.0 (not enforced) | restart | Low similarity threshold for confidence scoring. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_CONF_SIM_MID` | `0.55` |  | similarity threshold, usually 0.0-1.0 (not enforced) | restart | Middle similarity threshold for confidence scoring. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_CONVERSATIONS_DIR` | *unset* |  | directory path or empty | restart | Directory for persistent named conversations. | Empty disables named-conversation persistence; creation errors are ignored until use. |
| `ADAPTER_CONVERSATIONS_PER_USER` | `50` | `50` | positive count (not enforced here) | restart | Maximum named conversations retained per scope. | Unset or empty uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_CONVERSATION_MAX_TURNS` | `200` | `200` | positive turns (not enforced) | restart | Maximum stored turns per persistent conversation. | Unset or empty uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_CONVERSATION_RETENTION_DAYS` | `90` | `90` | non-negative days (not enforced) | restart | Retention age for stored conversations. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_CONVERSATION_TITLES` | `on` |  | off disables; any other value enables | restart | Enables background short title generation for conversations. | Only literal off disables generated titles. |
| `ADAPTER_CSR_COUNTRY` | `HU` |  | OpenSSL country string | restart | Country field placed into adapter TLS CSRs. | Accepted at startup; OpenSSL rejects invalid CSR subject data later. |
| `ADAPTER_CSR_ORG` | `ViVeTech` |  | organization string | restart | Organization field placed into adapter TLS CSRs. | Accepted at startup; OpenSSL rejects invalid CSR subject data later. |
| `ADAPTER_DIRECTORY` | `off` |  | off, token, entra | restart | Source of group and role membership. | Unset, empty or unknown is off (no groups or roles). |
| `ADAPTER_DIRECTORY_TIMEOUT_SECONDS` | `10` |  | positive seconds | restart | Timeout of one Microsoft Graph request. | Non-integer values fall back to 10 with a warning. |
| `ADAPTER_DIRECTORY_TTL_SECONDS` | `900` |  | non-negative seconds | restart | Cache lifetime of a directory lookup (refresh window). | Non-integer values fall back to 900 with a warning; an expired entry is never used when the directory is unreachable. |
| `ADAPTER_DISCOVERY` | `auto` | `auto` | auto \| on \| off \| 0 \| false \| no | restart | Controls SSDP discovery responder. | off/0/false/no disable SSDP; unknown values enable it. |
| `ADAPTER_DISCOVERY_LOCATION` | *unset* |  | absolute URL or empty | restart | Overrides the SSDP LOCATION URL. | Malformed values are advertised as-is and can break discovery clients. |
| `ADAPTER_DISCOVERY_MAXAGE` | `1800` |  | positive seconds (not enforced) | restart | SSDP Cache-Control max-age value. | Unset or empty uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_DISCOVERY_PORT` | `ADAPTER_PORT` |  | 1-65535 (not prevalidated) | restart | Advertised port in derived SSDP LOCATION URLs. | Unset or empty uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_DISCOVERY_ST` | `urn:vivesecbox-com:device:AIBox:1` | `urn:vivesecbox-com:device:AIBox:1` | SSDP service type | restart | SSDP ST value advertised by discovery. | Any string is accepted at startup; bad values fail only in the consumer if at all. |
| `ADAPTER_ENTITLEMENTS` | *unset* |  | JSON file path or empty | restart | Optional user-to-drive entitlement map. | Empty disables entitlements; missing or invalid JSON is treated as an empty map. |
| `ADAPTER_ENTRA_AUTHORITY` | `https://login.microsoftonline.com` |  | https URL | restart | Token authority (sovereign clouds). | Empty uses the public-cloud authority. |
| `ADAPTER_ENTRA_CLIENT_ID` | *unset* |  | application (client) id | restart | App registration used for Graph (client credentials). | Missing or invalid makes every Entra lookup return no groups. |
| `ADAPTER_ENTRA_CLIENT_SECRET_FILE` | *unset* |  | file path | restart | File holding the app registration's client secret. | Missing, unreadable or empty file makes every Entra lookup return no groups; the secret is never logged. |
| `ADAPTER_ENTRA_GRAPH_URL` | `https://graph.microsoft.com` |  | https URL | restart | Microsoft Graph endpoint. | Empty uses the public-cloud Graph; paging links outside it are refused. |
| `ADAPTER_ENTRA_RESOURCE_ID` | *unset* |  | service principal object id or empty | restart | Service principal whose app role assignments become the user's roles. | Empty reports no app roles (groups still resolve). |
| `ADAPTER_ENTRA_TENANT_ID` | *unset* |  | tenant GUID or domain | restart | Microsoft Entra ID tenant. | Missing or invalid makes every Entra lookup return no groups (reported at startup). |
| `ADAPTER_FEATURES` | *unset* | *empty* | comma/semicolon-separated ids | restart | Feature ids reported in adapter status. | Any ids are accepted; basic is always included. |
| `ADAPTER_FEEDBACK_DIR` | `/data/feedback` | `/data/feedback` | directory path or empty | restart | Directory for answer-rating JSONL records. | Empty disables feedback recording; unwritable paths make feedback requests fail. |
| `ADAPTER_FEEDBACK_TRACES` | `200` | `200` | positive count (not enforced) | restart | In-memory cap for answer traces joinable to feedback. | Unset or empty uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_FILES_DIR` | `/data/generated` | `/data/generated` | directory path or empty | restart | Session store for generated files. | Empty disables generated-file storage; unwritable paths fail save/download operations. |
| `ADAPTER_FILE_SEARCH_LIMIT` | `2000` |  | positive count (not enforced) | restart | Cap on flat filename search hits. | Unset uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_GENERATE` | `auto` | `auto` | auto \| on \| off | restart | Controls adapter answer synthesis. | off disables generation; auto skips when Ollama is down; unknown behaves like on. |
| `ADAPTER_GEN_MODEL` | `qwen2.5:14b` | `qwen3.6:35b` | Ollama model name | restart | Ollama chat model used by adapter generation. | Any string is accepted at startup; bad values fail only in the consumer if at all. |
| `ADAPTER_GETFILE_TIMEOUT` | `60` |  | positive seconds (not enforced) | restart | Timeout for fetching original documents from ViVeSecBox. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_HOST` | `127.0.0.1` | `0.0.0.0` | bind host/address | restart | Bind address for the plain HTTP adapter listener. | Invalid bind addresses raise OSError when the server starts. |
| `ADAPTER_HTTP_SCOPE` | `full` |  | full, pairing | restart | pairing limits the plain-HTTP listener to status/version/ui-version/init for network peers. | Unset or empty is full; any unknown value falls back to pairing. The mTLS listener and direct (not proxy-relayed) loopback callers are never restricted. |
| `ADAPTER_IDENTITY_ALGORITHMS` | `HS256,RS256` |  | comma list of HS256, RS256 | restart | Allowlist of JWT signature algorithms. | Unsupported names (including none) are ignored; an empty resulting list rejects every token. |
| `ADAPTER_IDENTITY_AUDIENCE` | *unset* |  | aud value or empty | restart | Audience the token must be issued for. | Empty skips the audience check. |
| `ADAPTER_IDENTITY_HEADER` | `VVS-Identity` |  | HTTP header name | restart | Request header carrying the signed identity assertion (JWT). | Empty uses VVS-Identity; a 'Bearer ' prefix in the value is tolerated. |
| `ADAPTER_IDENTITY_HS256_SECRET_FILE` | *unset* |  | file path or empty | restart | File holding the shared HS256 secret; the secret itself is never put in the environment. | Missing, unreadable or shorter than 32 bytes disables HS256 (such tokens are rejected). |
| `ADAPTER_IDENTITY_ISSUER` | *unset* |  | exact iss value or empty | restart | Required token issuer. | Empty skips the issuer check. |
| `ADAPTER_IDENTITY_JWKS_FILE` | *unset* |  | JWKS JSON file path or empty | restart | Public RS256 keys of the ViVeSecBox (JWKS). | Missing or malformed file yields no keys (RS256 tokens rejected); RSA keys under 2048 bits are skipped; reloaded on mtime change. |
| `ADAPTER_IDENTITY_LEEWAY_SECONDS` | `60` |  | non-negative seconds | restart | Clock-skew tolerance for exp/nbf/iat. | Non-integer values fall back to 60 with a warning; negative values count as 0. |
| `ADAPTER_IDENTITY_MAX_TTL_SECONDS` | `3600` |  | positive seconds | restart | Maximum accepted token lifetime. | Non-integer values fall back to 3600 with a warning; longer-lived tokens are rejected. |
| `ADAPTER_IDENTITY_MODE` | `off` |  | off, verify, require | restart | Whether user requests must carry a verified signed identity assertion. | Unset or empty is off (header identity, as before E02); any unknown value falls back to require. |
| `ADAPTER_IDENTITY_SCOPE` | *unset* |  | scope value or empty | restart | Scope value the token's scope/scp claim must contain. | Empty skips the scope check. |
| `ADAPTER_IDENTITY_USER_CLAIM` | `sub` |  | claim name | restart | Claim holding the stable user id (must equal VVS-User when both are sent). | Empty uses sub; a token without the claim is rejected. |
| `ADAPTER_INSIGHT_SCAN_LIMIT` | `200000` |  | positive integer | restart | Mirror entries one /api/v1/ui/insight call walks per drive. | A non-integer value stops the adapter at start-up; a drive with more entries is reported as truncated. |
| `ADAPTER_JOBS_DIR` | *unset* | `/data/jobs` | directory path or empty | restart | Directory for persistent job state. | Empty disables persistent jobs; creation errors are ignored until use. |
| `ADAPTER_JOB_MAX_PER_USER` | `50` | `50` | at least 1 after clamp | restart | Maximum persisted background jobs per scope; values below 1 are clamped to 1. | Unset or empty uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_JOB_QUEUE_MAX` | `5` | `5` | positive queue depth (not enforced) | restart | Maximum queued async UI jobs accepted. | Unset or empty uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_JOB_RETENTION_DAYS` | `7` | `7` | 0 or positive days after clamp | restart | Retention window for terminal jobs; negative values clamp to 0. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_LIFECYCLE_PATH` | *unset* |  | file path or empty | restart | Persistent state of user lifecycle locks (deleted/suspended/revoked). | Empty keeps lifecycle locks in memory only (lost on restart); an unreadable file refuses every user request (503) until repaired. |
| `ADAPTER_LONGPOLL_MAX` | `55` | `55` | positive seconds (not enforced) | restart | Upper bound for UI long-poll waits. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_LONGPOLL_SECONDS` | `25` | `25` | positive seconds (not enforced) | restart | Default UI long-poll wait. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_LUKS_DEVICE` | *unset* | *empty* | block device or file path | restart | Encrypted LUKS container device/file. | Empty is accepted at startup; unlocking in luks mode fails. |
| `ADAPTER_LUKS_FORMAT` | *unset* | *empty* | true strings: 1, true, yes, on | restart | Allows luksFormat and mkfs on first initialization. | Only 1/true/yes/on enables first-unlock formatting. |
| `ADAPTER_LUKS_FSTYPE` | `ext4` |  | mkfs suffix such as ext4 | restart | Filesystem created on first LUKS initialization. | Invalid values fail when mkfs.<type> is executed. |
| `ADAPTER_LUKS_MOUNT` | *unset* | *empty* | mount point or empty | restart | Mount point for decrypted storage. | Empty skips mounting; invalid paths fail during unlock. |
| `ADAPTER_LUKS_NAME` | `vivesec_data` | `vivesec_data` | device-mapper name | restart | dm-crypt mapper name under /dev/mapper. | Any string is accepted at startup; bad values fail only in the consumer if at all. |
| `ADAPTER_MAX_BODY_BYTES` | `536870912` |  | positive bytes (not enforced) | restart | Maximum request body bytes read into memory. | Unset uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_MAX_CONTEXT_TOKENS` | `4000` | `4000` | positive token budget (not enforced) | restart | General retrieval context budget. | Unset uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_META_PATH` | *unset* | `/data/adapter_meta.json` | file path or empty | restart | Persistent metadata mirror path. | Empty keeps metadata in memory; invalid paths fail on persistence. |
| `ADAPTER_NUM_CTX` | `0` | `65536` | 0 or positive Ollama num_ctx (not enforced) | restart | Ollama context window for normal chat; 0 omits option. | Unset or empty uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_NUM_PREDICT` | `512` | `2048` | 0 or positive Ollama num_predict (not enforced) | restart | Maximum generated tokens for adapter answers; 0 omits option. | Unset or empty uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_PKI_DIR` | `/data/pki` | `/data/pki` | directory path | restart | Directory for adapter PKI and stable discovery UUID. | Any path is accepted at startup; missing or unwritable paths disable persistence or fail when used. |
| `ADAPTER_PORT` | `8080` | `8088` | 1-65535 | restart | Plain HTTP adapter TCP port. | Unset uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_PUTFILE_RETRIES` | `3` | `3` | 0 or positive retries (not enforced) | restart | Retry count for ViVeSecBox put-file saves. | Unset or empty uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_PUTFILE_RETRY_DELAY` | `1.0` | `1.0` | 0 or positive seconds (not enforced) | restart | Delay between put-file retries. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_PUTFILE_TIMEOUT` | `30` | `30` | positive seconds (not enforced) | restart | Timeout per put-file attempt. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_RESET_GPIO_ACTIVE` | `0` |  | GPIO active value string | restart | Active GPIO value for factory-reset pin. | Accepted as-is; wrong values prevent reset detection. |
| `ADAPTER_RESET_GPIO_PATH` | *unset* |  | GPIO value file path | restart | File read by gpio reset monitor. | Empty disables gpio mode; unreadable paths prevent reset detection. |
| `ADAPTER_RESET_HOLD_SECONDS` | `5` | `5` | positive seconds (not enforced) | restart | Active duration required before factory reset. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_RESET_PIN_FILE` | *unset* |  | file path | restart | File read by file-mode reset monitor. | Empty disables file mode; unreadable paths prevent reset detection. |
| `ADAPTER_RESET_PIN_MODE` | `off` | `off` | off \| gpio \| file | restart | Selects factory-reset input source. | Only gpio and file create a reader; off or unknown disables the monitor. |
| `ADAPTER_SCOPE_ALL_DRIVES` (dev only) | *unset* |  | true strings: 1, on, true, yes | restart | Demo switch to search all known drives without per-user filtering. | Only 1/on/true/yes enables all-drive demo scope. |
| `ADAPTER_SESSION_DIR` | *unset* | `/data/sessions` | directory path or empty | restart | Spill directory for default-session chat state. | Empty keeps default sessions in memory; creation errors ignored until use. |
| `ADAPTER_SESSION_IDLE` | `900` | `900` | positive seconds (not enforced) | restart | Idle time before active session spill/expiry. | Unset or empty uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_SESSION_TURNS` | `12` | `12` | positive turn count (not enforced) | restart | Recent turns kept in active chat context. | Unset or empty uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_STORAGE_MODE` | `off` | `off` | off \| luks | restart | Selects no encryption layer or LUKS-backed storage. | Unknown values are accepted but behave as locked non-off storage. |
| `ADAPTER_STREAM` | `on` |  | off/false/0 disable; other values enable | restart | Controls Ollama streaming when caller does not override. | off/false/0 disable streaming; other values enable it. |
| `ADAPTER_STT_AUTODETECT` | *unset* |  | true strings: 1, true, yes, on | restart | Lets STT backend detect language instead of using UI hint. | Only 1/true/yes/on enables language autodetect. |
| `ADAPTER_STT_MAX_BYTES` | `26214400` |  | positive bytes (not enforced) | restart | Maximum uploaded audio size for STT. | Unset uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_STT_MODEL` | *unset* |  | STT model id or empty | restart | Optional model sent to STT backend. | Any string is accepted at startup; bad values fail only in the consumer if at all. |
| `ADAPTER_STT_TIMEOUT` | `120` |  | positive seconds (not enforced) | restart | Timeout for STT backend requests. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_STT_URL` | *unset* |  | transcription endpoint URL or empty | restart | STT endpoint; empty disables microphone transcription. | Any URL is accepted at startup; connection errors are reported when used. |
| `ADAPTER_TEMPERATURE` | `0.2` |  | Ollama temperature, usually 0.0-2.0 (not enforced) | restart | Temperature option sent to Ollama. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_TENANT_ID` | `default` | `default` | tenant id | restart | Default tenant id sent to RAG. | Any string is accepted at startup; bad values fail only in the consumer if at all. |
| `ADAPTER_THINK` | *unset* | `off` | off \| false \| 0 \| on \| true \| 1 \| empty | restart | Controls Ollama think field. | off/false/0 sends think:false; on/true/1 sends think:true; empty or unknown omits it. |
| `ADAPTER_TITLE_TIMEOUT` | `30` |  | positive seconds (not enforced) | restart | Timeout for title-generation call. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_TLS` | *unset* | *empty* | true strings: 1, true, yes, on | restart | Enables mutual-TLS listener after provisioning. | Only 1/true/yes/on enables TLS listener; uninitialized PKI leaves HTTP only. |
| `ADAPTER_TLS_HOSTNAME` | `local.aibox.vivesecbox.com` | `local.aibox.vivesecbox.com` | DNS hostname | restart | Hostname used in CSR and TLS checks. | Accepted at startup; provisioning/TLS can fail if cert names mismatch. |
| `ADAPTER_TLS_PORT` | `443` |  | 1-65535 | restart | Mutual-TLS adapter TCP port. | Unset uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_TTS_MAX_CHARS` | `2000` |  | positive chars (not enforced) | restart | Maximum text length accepted for TTS. | Unset or empty uses the code default; non-integer values raise ValueError and abort startup/import. |
| `ADAPTER_TTS_TIMEOUT` | `120` |  | positive seconds (not enforced) | restart | Timeout for TTS backend requests. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `ADAPTER_TTS_URL` | *unset* |  | TTS endpoint URL or empty | restart | TTS endpoint; empty disables spoken-answer capability. | Any URL is accepted at startup; connection errors are reported when used. |
| `ADAPTER_TTS_VOICES` | *unset* |  | comma-separated lang=voice map | restart | Optional voice map for TTS selection. | Any string is accepted at startup; bad values fail only in the consumer if at all. |
| `ADAPTER_UI_VERSION` | `latest` | `latest` | latest, or a release version (YY.MM.N) the embedding host actually serves | restart | Plain-text answer of GET /api/v1/ui-version: the embedded UI bundle path (/latest/ or /<version>/) the ViVeSecBox loads for this box. Not the box's own release version (see /api/v1/version). | Empty falls back to latest; any other string is returned verbatim, so a version the host does not serve leaves the embedded UI unloadable. |
| `ADAPTER_WATCHDOG_ENABLED` | `auto` | `auto` | auto \| on \| off | restart | Controls presence-lock watchdog. | on always enforces; auto enforces only when storage mode is not off; unknown disables. |
| `ADAPTER_WATCHDOG_SECONDS` | `90` | `90` | positive seconds (not enforced) | restart | Presence timeout before watchdog locks storage. | Unset uses the code default; non-integer values raise ValueError and abort startup/import. |
| `RAG_URL` | `http://127.0.0.1:8090` | `http://127.0.0.1:8090` | RAG service base URL | restart | Base URL used by adapter for RAG requests. | Any URL is accepted at startup; connection errors are reported when used. |

## RAG service

| Parameter | Default | Image | Allowed | Change | Impact | Invalid value |
| --- | --- | --- | --- | --- | --- | --- |
| `RAG_DEFAULT_TENANT` | `default` | `default` | tenant id | restart | Default tenant when requests omit one. | Any string is accepted at startup; bad values fail only in the consumer if at all. |
| `RAG_HOST` | `127.0.0.1` | `0.0.0.0` | bind host/address | restart | RAG service bind address. | Invalid bind addresses raise OSError when server starts. |
| `RAG_INDEX_PATH` | `rag_index.json next to service.py (json backend) / rag_index.db next to service.py (sqlite backend)` | `/data/rag_index.json` | file path | reindex | Persistent RAG index path; code default depends on RAG_STORE_BACKEND. | Any path is accepted at startup; missing or unwritable paths disable persistence or fail when used. |
| `RAG_MAX_FILE_BYTES` | `20971520` |  | positive bytes (not enforced) | restart | Files above this are metadata-only, not content-indexed. | Unset uses the code default; non-integer values raise ValueError and abort startup/import. |
| `RAG_MIN_SCORE` | `0` |  | positive similarity floor enables filtering | restart | Minimum cosine similarity for returned chunks; <=0 disables floor. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |
| `RAG_PORT` | `8090` | `8090` | 1-65535 | restart | RAG service TCP port. | Unset uses the code default; non-integer values raise ValueError and abort startup/import. |
| `RAG_QUERY_REACCENT` | `on` |  | false strings: 0, off, false, no, empty; other values true | restart | Repairs unaccented Hungarian queries from corpus vocabulary. | 0/off/false/no/empty disable; any other value enables. |
| `RAG_QUERY_SPLIT` | `conjunction` |  | off \| clause \| conjunction | restart | Splits conjunctive questions into independent retrieval queries. | off disables; conjunction adds bare-conjunction splitting; unknown behaves like clause-only. |
| `RAG_REACCENT_MAX_CHUNKS` | `50000` |  | positive chunks (not enforced) | restart | Maximum chunks scanned to build re-accent vocabulary. | Unset uses the code default; non-integer values raise ValueError and abort startup/import. |
| `RAG_SHEET_MAX_ROWS` | `5000` |  | positive rows (not enforced) | restart | Maximum spreadsheet rows extracted per sheet. | Unset uses the code default; non-integer values raise ValueError and abort startup/import. |
| `RAG_STORE_BACKEND` | `json` |  | json \| sqlite | reindex | Selects RAG store implementation. | Only exact sqlite selects sqlite-vec; unknown falls back to JSON store. |
| `RAG_TOKEN_TTL_SECONDS` | `900` |  | positive seconds (not enforced) | restart | Upload token lifetime for check -> content handshake. | Unset uses the code default; non-integer values raise ValueError and abort startup/import. |
| `VIVESEC_ANSWER_LANG` | *unset* |  | language name or empty | restart | PoC generation language override used by imported poc modules. | Any string is accepted at startup; bad values fail only in the consumer if at all. |
| `VIVESEC_BACKEND` | `auto` | `auto` | auto \| ollama \| fallback | reindex | Selects PoC embedding/generation backend. | fallback forces local fallback, ollama forces Ollama, unknown behaves like auto. |
| `VIVESEC_DATA` | `poc/data next to config.py` |  | directory path | reindex | PoC data directory used by imported modules. | Any path is accepted at startup; missing or unwritable paths disable persistence or fail when used. |
| `VIVESEC_EMBED_MODEL` | `bge-m3` |  | Ollama embedding model | reindex | Embedding model used by PoC/RAG indexing. | Any string is accepted at startup; bad values fail only in the consumer if at all. |
| `VIVESEC_GEN_MODEL` | `qwen2.5:3b` |  | Ollama generation model | restart | Generation model used by PoC generation layer. | Any string is accepted at startup; bad values fail only in the consumer if at all. |
| `VIVESEC_HOST` | `127.0.0.1` |  | bind host/address | restart | Bind address for legacy PoC HTTP bridge. | Invalid bind addresses raise OSError when poc/server.py starts. |
| `VIVESEC_INDEX` | `poc/index.json next to config.py` |  | file path | reindex | Legacy PoC index path. | Any path is accepted at startup; missing or unwritable paths disable persistence or fail when used. |
| `VIVESEC_NUM_PREDICT` | `512` |  | 0 or positive tokens (not enforced) | restart | Maximum generated tokens for PoC layer; 0 lets model decide. | Unset or empty uses the code default; non-integer values raise ValueError and abort startup/import. |
| `VIVESEC_PORT` | `8000` |  | 1-65535 | restart | TCP port for legacy PoC HTTP bridge. | Unset uses the code default; non-integer values raise ValueError and abort startup/import. |
| `VIVESEC_REPEAT_PENALTY` | `1.15` |  | positive repeat_penalty (not enforced) | restart | Repeat penalty sent by PoC generation layer. | Unset or empty uses the code default; non-float values raise ValueError and abort startup/import. |

## Shared (several components)

| Parameter | Default | Image | Allowed | Change | Impact | Invalid value |
| --- | --- | --- | --- | --- | --- | --- |
| `ADAPTER_DRIVE_PREFIX` | `/storage/drives` | `/storage/drives` | absolute drive root prefix | restart | Read by adapter corpus mapping and UI demo proxy driveRoot. | Any string is accepted; mismatches break adapter corpus ids and UI demo roots. |
| `OLLAMA_URL` | `adapter: http://127.0.0.1:11434; rag/poc: http://localhost:11434` | adapter: `http://127.0.0.1:11434`; rag: `http://localhost:11434` | Ollama base URL | restart | Read by adapter and rag/poc model clients; adapter and rag images set different defaults. | Any URL is accepted at startup; connection errors are reported when used. |
| `RAG_API_KEY` (secret) | *unset* |  | non-empty API key or empty | restart | Shared RAG API key read by adapter and RAG service. | Empty disables RAG API-key enforcement and adapter auth; non-empty values must match exactly. |

## Web UI server

| Parameter | Default | Image | Allowed | Change | Impact | Invalid value |
| --- | --- | --- | --- | --- | --- | --- |
| `ADAPTER_DEMO_DRIVE` | `/storage/drives/finance/` |  | drive root path | restart | Demo/LAN proxy drive when ViVeSecBox tunnel headers are absent. Used only by the box-local web UI server (no ViVeSecBox tunnel headers); the embedded UI never reads it, and the adapter still enforces scope. | Accepted as-is; wrong values send demo requests with wrong VVS-Drive. |
| `ADAPTER_DEMO_DRIVE_PICKER` (dev only) | *unset* |  | 1 enables; any other value disables | restart | Demo-only browser drive-picker switch. | Only literal 1 honors X-Demo-Drive; all other values ignore it. |
| `ADAPTER_DEMO_USER` | `demo` |  | user id | restart | Demo/LAN proxy user placed in VVS-User header. Used only by the box-local web UI server (no ViVeSecBox tunnel headers); the embedded UI never reads it, and the adapter still enforces scope. | Any string is accepted at startup; bad values fail only in the consumer if at all. |
| `ADAPTER_URL` | `http://127.0.0.1:80` |  | adapter base URL | restart | Server-side proxy target for relative /api/v1/* browser calls. | Any URL is accepted at startup; connection errors are reported when used. |

## Web UI build

| Parameter | Default | Image | Allowed | Change | Impact | Invalid value |
| --- | --- | --- | --- | --- | --- | --- |
| `VIVESEC_BASE` | *unset* |  | absolute base path or empty | install | Build-time pinned base path fallback for embedded SPA artifacts. | Invalid bases produce broken asset/router URLs. |
| `VIVESEC_SPA` | *unset* |  | 1 enables; any other value disables | install | Build-time switch for static SPA embedding. | Only literal 1 enables SPA mode unless --mode embed is used. |

## Installation kit

| Parameter | Default | Image | Allowed | Change | Impact | Invalid value |
| --- | --- | --- | --- | --- | --- | --- |
| `AIBOX_ADMIN_KEYS_FILE` | `/home/aibox/admin_keys.pub` |  | readable public-key file | install | Administrators' SSH public keys file. | Missing/unreadable file or invalid key lines abort preflight/hardening. |
| `AIBOX_HOSTNAME` | `aibox-01` |  | hostname string | install | Box identity for logs, Tailscale hostname and manifest. | Missing value aborts orchestrated install. |
| `AIBOX_REMOTE_ACCESS` | `tailscale` |  | tailscale \| none | install | Remote administration channel. | Only tailscale runs Tailscale setup; other values skip remote access. |
| `AIBOX_SSH_GRACE_SECONDS` | `900` |  | positive seconds (not prevalidated) | install | Window to confirm key SSH before reverting password hardening. | Non-integer values break shell arithmetic/timer behavior. |
| `AIBOX_SSH_NOWAIT` (dev only) | `0` |  | 1 skips waiting; other values wait | install | Automation knob for SSH hardening confirmation. | Only literal 1 skips the confirmation wait. |
| `AIBOX_SSH_USER` | `aibox` |  | existing local username | install | Login user hardened by SSH phase. | Nonexistent user aborts hardening. |
| `AIBOX_TS_TAGS` | `tag:aibox` |  | comma-separated Tailscale tags | install | Tags advertised when joining Tailscale. | Invalid tags make tailscale up fail. |
| `AUDIT_CONTINUE_ON_FAIL` | `0` |  | 1 continues; other values fail | install | Whether acceptance audit failures abort install. | Only literal 1 lets audit exit 0 after failed checks. |
| `AUDIT_MIN_FREE_GB` | `50` |  | non-negative GiB (not prevalidated) | install | Minimum free /data space required by audit. | Non-integer values make audit comparison fail. |
| `EMBED_MODEL` | `bge-m3:latest` |  | Ollama model name | install | Embedding model pulled and verified. | Missing value aborts install; invalid names fail pull/verification. |
| `GEN_MODEL` | `qwen3.6:35b` |  | Ollama model name | install | Generation model pulled and verified. | Missing value aborts install; invalid names fail pull/verification. |
| `MANIFEST_PATH` | `/data/app/MANIFEST.txt` |  | output file path | install | Delivery manifest output path. | Unwritable paths make manifest writing fail. |
| `NVME_DEVICE` | `/dev/nvme0n1` |  | /dev/nvme0n1 | install | NVMe device the installer may verify and erase. | Missing value or safety-check mismatch aborts provisioning. |
| `NVME_MODEL` | `AFOX SSD ME300-512GN` |  | exact lsblk model | install | Expected NVMe model safety guard. | Model mismatch aborts NVMe provisioning/preflight. |
| `NVME_SERIAL` | `CHANGE-ME` |  | exact lsblk serial | install | Expected NVMe serial safety guard. | Missing value or CHANGE-ME placeholder aborts install; mismatch aborts provisioning. |
| `NVME_SIZE_BYTES` | `512110190592` |  | exact byte count | install | Expected NVMe byte size safety guard. | Missing or non-matching value aborts NVMe provisioning. |
| `OLLAMA_VERSION` | `0.34.0` |  | Ollama version | install | Ollama version installed and checked. | Missing value aborts install; invalid versions fail runtime install/audit. |
| `RAG_STAGE` | `/data/app/rag-src` |  | directory path | install | Temporary RAG Docker build staging directory. | Unwritable paths fail image staging; existing directory is removed. |
| `SOURCE_DIR` | `/home/aibox/vivesec_iabox_app` |  | existing source tree | install | Delivered source tree for image builds and manifest. | Missing value or required subpaths abort preflight/build. |
| `TS_AUTH_KEY` (secret) | *unset* |  | Tailscale auth key or empty | install | Pre-authorized Tailscale auth key passed via environment. | Absence only warns when Tailscale is selected; invalid keys make tailscale up fail. |
| `TS_AUTH_KEY_FILE` (secret) | *unset* |  | readable file containing Tailscale auth key | install | File alternative for Tailscale auth key. | If used and unreadable, hardening fails when loading the key. |
| `UI_BUNDLE` | `/home/aibox/ui-output.tgz` |  | readable .tgz | install | Pre-built UI bundle for Jetson runtime image. | Missing/unreadable/malformed archives abort UI image build. |
| `UI_STAGE` | `/data/app/ui-src` |  | directory path | install | Temporary UI Docker build staging directory. | Unwritable paths fail image staging; existing directory is removed. |
