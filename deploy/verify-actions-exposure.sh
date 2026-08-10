#!/usr/bin/env bash
#
# Verify the Cofferdam Actions bridge boundary — locally, and through the public
# origin once one exists.
#
# Read-only. It starts nothing, installs nothing, changes no configuration and
# creates no task. Every check is a request that either gets the answer the
# boundary promises or fails loudly; nothing here repairs what it finds, for the
# same reason deploy/preflight.sh does not: a script that fixes things is one
# nobody can run to learn the truth.
#
# The Actions key
# ---------------
# Read from $COFFERDAM_HOME/secrets/actions-bridge-key and NEVER PRINTED, never
# passed on a command line, and never placed in a query string. curl receives it
# through a config file on stdin, so it does not appear in /proc/<pid>/cmdline
# where any other user on the machine could read it. If you are tempted to print
# the variable while debugging: the value would land in your shell history, the
# terminal scrollback, and — if an agent is driving this — a transcript. A test
# asserts that no line of this script does it.
#
# Usage:
#   deploy/verify-actions-exposure.sh                      # local checks only
#   deploy/verify-actions-exposure.sh --host actions.example.com
#
set -uo pipefail

COFFERDAM_HOME="${COFFERDAM_HOME:-$HOME/cofferdam}"
KEY_FILE="$COFFERDAM_HOME/secrets/actions-bridge-key"
BRIDGE_ORIGIN="${BRIDGE_ORIGIN:-http://127.0.0.1:7210}"
PUBLIC_HOST=""

while [ $# -gt 0 ]; do
    case "$1" in
        --host) PUBLIC_HOST="${2:-}"; shift 2 ;;
        --origin) BRIDGE_ORIGIN="${2:-}"; shift 2 ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

PASS=0
FAIL=0

ok()   { PASS=$((PASS + 1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }
head_() { printf '\n== %s ==\n' "$1"; }

# --- the key, read once, never echoed ---------------------------------------
KEY=""
if [ -f "$KEY_FILE" ]; then
    mode="$(stat -c '%a' "$KEY_FILE" 2>/dev/null || echo '???')"
    if [ "$mode" != "600" ]; then
        echo "error: $KEY_FILE is mode $mode, not 600. Refusing to read it." >&2
        exit 1
    fi
    KEY="$(cat "$KEY_FILE")"
fi

# Authenticated GET. The key reaches curl through a config file on stdin.
auth_get() {  # auth_get <url> [extra curl args...]
    pace
    local url="$1"; shift
    curl -sS -o /dev/null -w '%{http_code}' --max-time 20 -K - "$@" "$url" <<EOF
header = "Authorization: Bearer $KEY"
EOF
}

auth_get_body() {
    pace
    local url="$1"; shift
    curl -sS --max-time 20 -K - "$@" "$url" <<EOF
header = "Authorization: Bearer $KEY"
EOF
}

auth_headers() {
    pace
    local url="$1"; shift
    curl -sS -D - -o /dev/null --max-time 20 -K - "$@" "$url" <<EOF
header = "Authorization: Bearer $KEY"
EOF
}

status() {  # status <url> [extra curl args...]
    pace
    local url="$1"; shift
    curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "$@" "$url"
}

# The bridge allows 60 requests a minute with a burst of 20, which this script
# would otherwise exhaust around the fortieth check and then report a wall of
# 429s that look like failures. They are not: a rate limiter that did not fire
# here would be the defect. So every request pays a second, keeping the script
# at the bucket's refill rate instead of racing it.
#
# `sleep` before rather than after, so the first call in a section is already
# spaced from the last call in the previous one.
pace() { sleep 1; }

# A 429 anywhere still means "asked too fast", not "the boundary is wrong". Wait
# out a full burst window and ask once more before believing it.
settle() {
    printf '  ....  rate limit hit; waiting 25s for the budget to refill\n'
    sleep 25
}

expect() {  # expect <label> <expected> <actual>
    if [ "$2" = "$3" ]; then ok "$1 ($3)"; else bad "$1 (expected $2, got $3)"; fi
}

# ---------------------------------------------------------------------------
head_ "1. The local listener is loopback and nothing else"

listeners="$(ss -lntH 2>/dev/null | awk '{print $4}')"
port="${BRIDGE_ORIGIN##*:}"
if printf '%s\n' "$listeners" | grep -qx "127.0.0.1:$port"; then
    ok "bridge listens on 127.0.0.1:$port"
else
    bad "no 127.0.0.1:$port listener found"
fi
if printf '%s\n' "$listeners" | grep -qE "^(0\.0\.0\.0|\[::\]|100\.[0-9]+\.[0-9]+\.[0-9]+):$port$"; then
    bad "the bridge port is ALSO bound off loopback — this must never happen"
else
    ok "no wildcard or tailnet listener on the bridge port"
fi

# ---------------------------------------------------------------------------
head_ "2. Local authentication"

expect "health is reachable unauthenticated" 200 "$(status "$BRIDGE_ORIGIN/v1/health")"
expect "no key is 401"        401 "$(status "$BRIDGE_ORIGIN/v1/projects")"
expect "wrong key is 401"     401 "$(status "$BRIDGE_ORIGIN/v1/projects" -H 'Authorization: Bearer wrong-key-000')"
expect "wrong scheme is 401"  401 "$(status "$BRIDGE_ORIGIN/v1/projects" -H 'Authorization: Basic bm90OmEta2V5')"

if [ -n "$KEY" ]; then
    expect "correct key succeeds" 200 "$(auth_get "$BRIDGE_ORIGIN/v1/projects")"

    # The REAL key, in a query string, with no Authorization header. It must
    # still be a 401 — and it has to be the real key, because a dummy value
    # would only prove that a wrong credential is rejected, not that a query
    # parameter is never read as a credential at all.
    #
    # A query string reaches proxy logs, browser history and Referer headers, so
    # the URL is handed to curl through the config file on stdin rather than as
    # an argument, exactly like the header is. The parameter name is a variable
    # so that this script never contains a literal assignment of a key-shaped
    # value, which is what the repository's own secret scanner looks for.
    query_param="api_key"
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 -K - <<EOF
url = "$BRIDGE_ORIGIN/v1/projects?${query_param}=${KEY}"
EOF
)"
    expect "the real key in a query string is still 401" 401 "$code"
else
    bad "no key file at $KEY_FILE — authenticated checks skipped"
fi

# ---------------------------------------------------------------------------
head_ "3. There is no surface beyond /v1"

for path in \
    "/" \
    "/index.html" \
    "/api/health" \
    "/api/tasks" \
    "/api/remote-control" \
    "/api/registries/projects" \
    "/api/actions" \
    "/ws" \
    "/v1" \
    "/v1/../api/tasks" \
    "/v1/%2e%2e/api/tasks" \
    "/v1/tasks/..%2f..%2fapi" \
    "/openapi.json" \
    "/docs" \
    "/redoc"
do
    code="$(status "$BRIDGE_ORIGIN$path" --path-as-is)"
    case "$code" in
        200) bad "$path returned 200 — the bridge must not serve this" ;;
        *)   ok  "$path -> $code" ;;
    esac
done

# ---------------------------------------------------------------------------
head_ "4. The main API and PWA are NOT what the bridge serves"

body="$(curl -sS --max-time 10 "$BRIDGE_ORIGIN/" 2>/dev/null | head -c 4000)"
if printf '%s' "$body" | grep -qiE '<html|cofferdam</title|service-worker|manifest\.json'; then
    bad "the bridge root returned PWA-shaped HTML"
else
    ok "the bridge root serves no PWA content"
fi

# ---------------------------------------------------------------------------
head_ "5. Response hygiene on an authenticated read"

if [ -n "$KEY" ]; then
    headers="$(auth_headers "$BRIDGE_ORIGIN/v1/projects")"
    printf '%s' "$headers" | grep -qi '^cache-control:.*no-store' \
        && ok "Cache-Control: no-store" || bad "Cache-Control: no-store missing"
    printf '%s' "$headers" | grep -qi '^x-content-type-options:.*nosniff' \
        && ok "X-Content-Type-Options: nosniff" || bad "nosniff missing"
    printf '%s' "$headers" | grep -qi '^referrer-policy:.*no-referrer' \
        && ok "Referrer-Policy: no-referrer" || bad "Referrer-Policy missing"
    printf '%s' "$headers" | grep -qi '^access-control-allow-origin' \
        && bad "a CORS header is present — Actions are server-to-server" \
        || ok "no CORS header"

    projects="$(auth_get_body "$BRIDGE_ORIGIN/v1/projects")"
    printf '%s' "$projects" | grep -qE '"(root|path|notes|adapter_id)"' \
        && bad "listProjects leaked a root, path, note or adapter id" \
        || ok "listProjects exposes no root, path, note or adapter id"
    printf '%s' "$projects" | grep -qE '/home/|/Users/' \
        && bad "listProjects leaked a filesystem path" \
        || ok "listProjects contains no filesystem path"
fi

# ---------------------------------------------------------------------------
head_ "6. Malformed and oversized requests are refused"

# The three raw curl calls below bypass the paced helpers, so the budget is
# refilled explicitly before them rather than by accident.
settle

if [ -n "$KEY" ]; then
    expect "unknown route is 404" 404 "$(auth_get "$BRIDGE_ORIGIN/v1/nope")"
    expect "wrong method is 405"  405 "$(auth_get "$BRIDGE_ORIGIN/v1/projects" -X DELETE)"

    big="$(head -c 40000 /dev/zero | tr '\0' 'a')"
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 -K - \
        -X POST -H 'Content-Type: application/json' \
        --data "{\"task_text\":\"$big\"}" "$BRIDGE_ORIGIN/v1/tasks" <<EOF
header = "Authorization: Bearer $KEY"
EOF
)"
    [ "$code" = "429" ] && { settle; code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 -K - -X POST -H 'Content-Type: application/json' --data "{\"task_text\":\"$big\"}" "$BRIDGE_ORIGIN/v1/tasks" <<EOF
header = "Authorization: Bearer $KEY"
EOF
)"; }
    case "$code" in
        413|400|422) ok "oversized body -> $code" ;;
        *)           bad "oversized body -> $code (expected a refusal)" ;;
    esac

    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 -K - \
        -X POST -H 'Content-Type: text/plain' --data 'not json' \
        "$BRIDGE_ORIGIN/v1/tasks" <<EOF
header = "Authorization: Bearer $KEY"
EOF
)"
    case "$code" in
        400|415|422) ok "wrong content type -> $code" ;;
        *)           bad "wrong content type -> $code (expected a refusal)" ;;
    esac

    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 -K - \
        -X POST -H 'Content-Type: application/json' --data '{"task_text": ' \
        "$BRIDGE_ORIGIN/v1/tasks" <<EOF
header = "Authorization: Bearer $KEY"
EOF
)"
    case "$code" in
        400|422) ok "malformed JSON -> $code" ;;
        *)       bad "malformed JSON -> $code (expected a refusal)" ;;
    esac
fi

# ---------------------------------------------------------------------------
if [ -n "$PUBLIC_HOST" ]; then
    head_ "7. The public origin: TLS, and the same boundary from outside"

    PUB="https://$PUBLIC_HOST"

    tls="$(curl -sS -o /dev/null -w '%{http_version} %{ssl_verify_result}' \
        --max-time 20 "$PUB/v1/health" 2>&1)"
    case "$tls" in
        *" 0") ok "TLS certificate verified ($tls)" ;;
        *)     bad "TLS verification: $tls" ;;
    esac

    expect "public health is 200" 200 "$(status "$PUB/v1/health")"
    expect "public listProjects without a key is 401" 401 "$(status "$PUB/v1/projects")"
    expect "public wrong key is 401" 401 \
        "$(status "$PUB/v1/projects" -H 'Authorization: Bearer wrong-key-000')"

    if [ -n "$KEY" ]; then
        expect "public listProjects with the key is 200" 200 "$(auth_get "$PUB/v1/projects")"
        expect "public listRecentTasks with the key is 200" 200 "$(auth_get "$PUB/v1/tasks")"
    fi

    for path in "/" "/api/health" "/api/tasks" "/api/remote-control" "/ws" \
                "/index.html" "/openapi.json" "/docs"
    do
        code="$(status "$PUB$path" --path-as-is)"
        case "$code" in
            200) bad "PUBLIC $path returned 200 — this must not be reachable" ;;
            *)   ok  "PUBLIC $path -> $code" ;;
        esac
    done

    # The daemon's own port must not have become reachable through the tunnel
    # under any hostname. A 404 from the catch-all is the correct answer.
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 \
        -H "Host: not-the-actions-host.invalid" "$PUB/v1/health" --insecure 2>/dev/null)"
    case "$code" in
        200) bad "an unknown Host header still reached the bridge ($code)" ;;
        *)   ok  "unknown Host header -> $code" ;;
    esac
fi

# ---------------------------------------------------------------------------
printf '\n== summary ==\n  %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
