#!/bin/bash
# Mint a read-only kubeconfig for the k8srca-reader ServiceAccount.
#
#   kubectl apply -f rbac/k8srca-readonly.yaml
#   ./rbac/make-reader-kubeconfig.sh [output-path] [duration]
#
# Uses a bound, time-limited token (`kubectl create token`) rather than a
# long-lived Secret. Tokens expire: re-run this to rotate. Default 1 year --
# shorten it if you have somewhere to automate rotation.
set -euo pipefail

OUT="${1:-$HOME/.kube/k8srca-reader.yaml}"
DURATION="${2:-8760h}"
SA=k8srca-reader
NS=kube-system

command -v kubectl >/dev/null || { echo "kubectl not found" >&2; exit 1; }
kubectl get sa "$SA" -n "$NS" >/dev/null 2>&1 || {
  echo "ServiceAccount $NS/$SA not found. Run: kubectl apply -f rbac/k8srca-readonly.yaml" >&2
  exit 1
}

SERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
CA=$(kubectl config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')
if [[ -z "$CA" ]]; then
  CAFILE=$(kubectl config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority}')
  CA=$(base64 -w0 < "$CAFILE")
fi
TOKEN=$(kubectl create token "$SA" -n "$NS" --duration="$DURATION")

mkdir -p "$(dirname "$OUT")"
cat > "$OUT" <<YAML
apiVersion: v1
kind: Config
clusters:
- name: k8srca
  cluster:
    server: ${SERVER}
    certificate-authority-data: ${CA}
contexts:
- name: k8srca
  context: {cluster: k8srca, user: ${SA}, namespace: default}
current-context: k8srca
users:
- name: ${SA}
  user:
    token: ${TOKEN}
YAML
chmod 600 "$OUT"
echo "wrote $OUT (expires in $DURATION)"

# The point of the exercise: prove it cannot write.
echo "verifying:"
for v in create delete patch; do
  r=$(kubectl auth can-i "$v" pods -A --kubeconfig "$OUT" 2>&1 | tail -1)
  printf "  %-7s pods: %s%s\n" "$v" "$r" "$([ "$r" = no ] || echo '   <-- EXPECTED no')"
done
printf "  %-7s pods: %s\n" "list" "$(kubectl auth can-i list pods -A --kubeconfig "$OUT" 2>&1 | tail -1)"
printf "  %-7s pods/log: %s\n" "get" "$(kubectl auth can-i get pods/log -A --kubeconfig "$OUT" 2>&1 | tail -1)"
printf "  %-7s secrets: %s\n" "get" "$(kubectl auth can-i get secrets -A --kubeconfig "$OUT" 2>&1 | tail -1)"
