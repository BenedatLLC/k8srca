# Pointing k8srca at a cluster

k8srca reads a cluster through `k8stools`, which runs in its own container and
is **the only process that holds a kubeconfig** (design 001 §4.1, §8.2). Nothing
else in the system — not the orchestrator, not the sandbox running
model-authored `bash` — ever sees the credential.

Which makes the credential you mount the thing that matters most here.

## 1. Create the read-only ServiceAccount

```bash
kubectl apply -f rbac/k8srca-readonly.yaml
```

Creates `k8srca-reader` in `kube-system`, a ClusterRole with `get`/`list`/`watch`
on the resources k8stools reads plus `pods/log`, and the binding. Deliberately
absent: **no `secrets`**, no write verbs on anything, no `pods/exec`.

This is layer four of the read-only guarantee — the backstop that holds if
every other layer fails. It is worth having even though k8stools exposes no
mutating tools.

## 2. Mint a kubeconfig for it

```bash
./rbac/make-reader-kubeconfig.sh                     # ~/.kube/k8srca-reader.yaml
./rbac/make-reader-kubeconfig.sh /path/to/out 720h   # or: custom path, 30 days
```

It prints a verification block. The three `no` answers are the point:

```
  create  pods: no
  delete  pods: no
  patch   pods: no
  list    pods: yes
  get     pods/log: yes
  get     secrets: no
```

Tokens are bound and time-limited rather than permanent Secrets. Re-run the
script to rotate.

## 3. Point k8srca at it

```bash
# .env
K8SRCA_KUBECONFIG=/home/you/.kube/k8srca-reader.yaml
```

Then:

```bash
docker compose -f docker/compose.yaml up -d k8stools
```

Verify the whole tool surface works under the restricted role before relying
on it — an incomplete ClusterRole surfaces as tool failures mid-investigation,
not at startup:

```bash
uv run k8srca tools validate
```

## Checking an existing credential

If you already have a kubeconfig and want to know whether it is safe to mount:

```bash
kubectl auth can-i create pods -A --kubeconfig <file>    # want: no
kubectl auth can-i get secrets -A --kubeconfig <file>    # want: no
```

A `yes` does not make the agent dangerous — k8stools has no mutating tools and
the sandbox never sees the file — but it removes the backstop, leaving the
guarantee resting on the other three layers alone.

## Troubleshooting

**`error validating data: failed to download openapi: the server could not
find the requested resource`**

Client-side validation could not fetch the API server's OpenAPI schema. Almost
always transient — a busy or briefly unavailable API server. **Retry first.**

If it persists:

```bash
kubectl get --raw /openapi/v3 | head -c 100    # should return JSON
kubectl get apiservices | grep -i false        # a broken aggregated API breaks discovery
kubectl version                                # large client/server skew
```

A broken `APIService` (an unavailable metrics-server is the usual one) breaks
discovery for everything. `--validate=false` will push the apply through, but
it skips validation rather than fixing the cause — prefer a retry.

**Tools return `forbidden` mid-investigation**

The ClusterRole is missing a resource. Add it to `rbac/k8srca-readonly.yaml`,
re-apply, and re-run `k8srca tools validate`.
