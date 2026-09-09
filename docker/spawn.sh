#!/bin/bash
# Called once per claimed work item. The work item arrives as JSON on stdin.
# Design 001 §7.3.
set -euo pipefail

: "${ANTHROPIC_SESSION_ID:?}" "${ANTHROPIC_WORK_ID:?}" "${ANTHROPIC_ENVIRONMENT_ID:?}"
: "${ANTHROPIC_ENVIRONMENT_KEY:?}"

# The per-session secret is NOT exported into the spawn script by the poller;
# read it from the work item on stdin. Omitting it works today and breaks v2
# memory stores at claim time.
if [[ -z "${ANTHROPIC_WORK_SECRET:-}" ]]; then
  ANTHROPIC_WORK_SECRET="$(jq -r '.secret // empty' 2>/dev/null || true)"
fi
export ANTHROPIC_WORK_SECRET

# The container is per TURN; the workspace is per SESSION. A tmpfs here would
# be wiped after every turn -- skills would re-download each time and nothing
# would survive to the follow-up question (001 §3.2). This looks fine in a
# one-shot test and fails on the second message in a thread.
WS="${K8SRCA_WORKSPACES:-$PWD/.k8srca/workspaces}/${ANTHROPIC_SESSION_ID}"
mkdir -p "$WS"

exec docker run --rm \
  --name "k8srca-sbx-${ANTHROPIC_WORK_ID:0:24}" \
  --network "${K8SRCA_NETWORK:-k8srca-net}" \
  --memory "${K8SRCA_SANDBOX_MEMORY:-2g}" \
  --cpus "${K8SRCA_SANDBOX_CPUS:-2}" \
  --pids-limit 512 \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev \
  -v "$WS:/workspace" \
  -e ANTHROPIC_SESSION_ID -e ANTHROPIC_WORK_ID -e ANTHROPIC_ENVIRONMENT_ID \
  -e ANTHROPIC_ENVIRONMENT_KEY -e ANTHROPIC_WORK_SECRET \
  -e ANTHROPIC_BASE_URL -e K8SRCA_MANIFESTS -e K8SRCA_LOG_LEVEL \
  "${K8SRCA_SANDBOX_IMAGE:?}"
