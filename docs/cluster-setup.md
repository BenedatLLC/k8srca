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


## When the API server is not reachable from a container

The containerised path needs the **k8stools container** to reach the cluster
API server. That is trivial when the API server is on a routable address, and
awkward in two common development setups:

- **An SSH tunnel** (`ssh -L 6443:...`), which binds `127.0.0.1` by default.
- **A local minikube/kind** whose API server is on the host loopback.

A container cannot reach the host's loopback. `kubectl` works on the host and
k8stools fails inside a container with a connection error.

Check which situation you are in:

```bash
grep server: <your-kubeconfig>            # localhost/127.0.0.1 means you are affected
ss -ltnp | grep 6443                      # what is actually listening
```

Three ways out, cheapest first.

### 1. Bind the tunnel to the docker gateway (recommended)

*Verified working on the demo cluster.*

If the API server comes to you over SSH, add a second forward bound to the
docker bridge instead of loopback:

```bash
GW=$(docker network inspect k8srca-net -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}')
ssh -L "${GW}:6443:localhost:6443" <your-host>     # alongside your existing -L
```

One line, no extra container, and it exposes the API server only on the docker
bridge — not the LAN. Then point the container's kubeconfig at it:

```yaml
clusters:
- name: k8srca
  cluster:
    server: https://172.20.0.1:6443     # the gateway
    tls-server-name: localhost          # keep certificate verification working
    certificate-authority-data: ...
```

`tls-server-name` matters: the API server's certificate is issued for names
like `localhost` and `kubernetes`, not for the gateway address. Setting it
preserves verification rather than disabling it with
`insecure-skip-tls-verify`.

### 2. Run k8stools on the host network

```yaml
# docker/compose.yaml, k8stools service
network_mode: host
```

It then reaches `127.0.0.1:6443` directly. The trade is that k8stools loses
network isolation — acceptable in that it is the trusted component that holds
the credential anyway, but the sandbox must then reach it across the docker
gateway rather than by service name, which the egress rules deny by default.

### 3. Stay on the in-process worker for this cluster

`k8srca worker` runs on the host and reaches the tunnel with no bridging at
all. **This is a development shape only**: it runs agent-authored bash on your
machine. Credentials are scrubbed from its environment, but that is defence in
depth, not isolation. See design 001 §8.2.

## Why the egress rules exempt k8stools

`docker/egress-rules.sh` denies the sandbox network any private destination —
the host, the LAN, cloud metadata, and in most deployments the API server
itself. k8stools sits on that same network and legitimately needs the API
server, so it is exempted **by source IP**:

```bash
sudo ./docker/egress-rules.sh apply
#   exempting k8stools at 172.20.0.2 (needs the API server)
```

Start k8stools before applying the rules, or the script cannot find it; it
warns rather than silently producing a configuration that breaks the very
component it is meant to leave working.


## Two things that bite in the containerised path

**A read-only container needs a writable `/tmp`.** `k8stools` is run with
`read_only: true`, and the Kubernetes client needs a temp directory. Without
one, *every* tool fails at runtime with:

```
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', ...]
mcp...UnexpectedToolError: Error executing tool get_node_summaries
```

The compose file mounts a small tmpfs. Note the mock profile does **not**
reproduce this — mock tools never call the API — so it only appears the first
time you point at a real cluster.

**A mode-600 kubeconfig is unreadable by the image's own user.** Which is
correct for a credential, so the container runs as the file's owner instead:

```bash
K8SRCA_UID=$(id -u) K8SRCA_GID=$(id -g) \
  docker compose -f docker/compose.yaml up -d k8stools
```

`.env.container` (gitignored) is a convenient place to keep that plus the
container-side `K8SRCA_KUBECONFIG`.
