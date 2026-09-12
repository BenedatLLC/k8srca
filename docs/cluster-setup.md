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
# ...or just set K8SRCA_SSH_HOST / K8SRCA_SSH_REMOTE and let `k8srca up` do it
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


## Surviving a reboot

A reboot destroys three things. Docker containers with `restart: unless-stopped`
come back on their own; these do not:

| Lost on reboot | Restored by |
| --- | --- |
| SSH forward to the docker gateway | `k8srca-tunnel` user unit (`Restart=always`) |
| The generated container kubeconfig | `k8srca up` — regenerated, not cached |
| iptables egress rules | `egress-rules.sh apply` (system unit, needs root) |

### `k8srca up`

Idempotent bring-up of everything that does not need root. Safe to run at boot,
and useful as a diagnostic at any time:

```
ok    docker network         k8srca-net subnet=172.20.0.0/16 gateway=172.20.0.1
ok    cluster access         mode=ssh_tunnel server=https://localhost:6443
ok    ssh tunnel             started, listening on 172.20.0.1:6443  (changed)
ok    container kubeconfig   ~/.kube/...-container.yaml -> https://172.20.0.1:6443 (tls-server-name=localhost)  (changed)
ok    k8stools               started with /home/you/.kube/...-container.yaml  (changed)
FAIL  egress rules           sandbox can reach the API server -- run: sudo ./docker/egress-rules.sh apply
```

It reports the egress rules rather than applying them, because they need root
and it does not.

### Install the units

```bash
./systemd/install.sh
```

That installs the **user** units (no root) and offers to enable linger for
you. It prints the one command it cannot run: the **system** unit for the
egress rules, which needs root.

**Linger matters more than it sounds.** systemd user units do not start at boot
unless your user lingers — they wait for a first interactive login. Without it
the supervised tunnel is simply absent after a reboot, and the symptom is the
agent reporting it cannot reach the cluster while `kubectl` works fine.

`k8srca status` warns whenever units are enabled and linger is off:

```
warn  boot persistence       k8srca-tunnel.service enabled but linger is OFF -- they will NOT start
        until you log in. Fix: sudo loginctl enable-linger you
```

It is a warning, not a failure: nothing is broken *now*, only after the next
reboot.

The egress unit waits for the k8stools container before applying, because the
rules exempt it by source IP — applying them first would install rules that
deny k8stools the API server.

## What is site-specific, and what is not

Only one thing: **how to reach the API server when it is not routable from a
container.** In `k8srca.yaml`:

```yaml
# k8srca.yaml -- version-controlled, portable
cluster_access:
  mode: auto
  kubeconfig: ~/.kube/k8srca-reader.yaml
  container_kubeconfig: ~/.kube/k8srca-reader-container.yaml
```

```bash
# .env -- never committed
K8SRCA_SSH_HOST=bastion.example.com
K8SRCA_SSH_REMOTE=192.168.49.2:8443
# K8SRCA_SSH_PORT=6443   # optional, defaults to 6443
```

The tunnel details are in `.env` rather than `k8srca.yaml` deliberately. A
bastion address and an internal API-server address are infrastructure
topology; committing them would make the config non-portable in exactly the
way the rest of it is portable, and would carry your network layout into any
copy of the repository.

`mode: auto` reads the kubeconfig: a loopback server cannot be reached from a
container, so a tunnel is required; anything routable is used as-is. Drop the
`ssh` block entirely for a cluster whose API server is directly reachable.

Everything else is derived when it is needed, so the same file works on another
machine:

| Value | Derived from |
| --- | --- |
| docker gateway, subnet, bridge | `docker network inspect` |
| container kubeconfig server | gateway + configured port |
| `tls-server-name` | the API server's certificate SANs, preferring `localhost` |
| uid/gid for the mount | the invoking user |
| k8stools IP for the egress exemption | the running container |

The `tls-server-name` derivation is what keeps certificate verification working
when the container connects to the gateway rather than to the address in the
original kubeconfig. It asks the certificate what names it will answer to,
rather than disabling verification.


## The failure this design invites

The container reaches the API server through a **different forward** than
`kubectl` and `k9s` do. Yours is on loopback; k8srca's is on the docker
gateway. They fail independently.

So the shape to recognise is: **the agent says it cannot reach the cluster
while your own tools work fine.** That is not the agent being confused — it is
the gateway forward being down while the loopback one is up.

```bash
ss -ltn | grep 6443        # both 127.0.0.1 and the gateway should be listed
uv run k8srca status       # `cluster reachable` calls a real tool end to end
uv run k8srca up           # idempotent; restores the forward
```

Two things exist because this happened:

- **`k8srca-tunnel`** is a user unit with `Restart=always`. `k8srca up` starts
  the forward once; nothing was restarting it when the connection dropped.
- **`k8srca status` calls a live tool** rather than only checking that
  processes are running. It previously reported every step green while the
  data path was dead — which is worse than reporting nothing, because it sends
  you looking in the wrong place.


## Adding declared state (`chart_repo`)

The live cluster says what is running. A chart or manifest source says what is
*supposed* to be running, and the gap between them is drift — often the fastest
route to "what changed".

```yaml
# k8srca.yaml
architecture:
  sources:
    - type: live_cluster
      server: k8stools
      namespaces: [default]
    - type: chart_repo
      path: .k8srca/sources        # rendered manifests or plain YAML
```

```bash
uv run k8srca arch build
uv run k8srca sync
uv run skills/cluster-architecture/arch_query.py drift
```

**Point it at rendered output, not raw Helm charts.** Unrendered templates are
skipped rather than guessed at:

```bash
helm template my-release ./chart > .k8srca/sources/rendered.yaml
# or, for a project that publishes one:
curl -fsSL -o .k8srca/sources/demo.yaml \
  https://raw.githubusercontent.com/open-telemetry/opentelemetry-demo/2.2.0/kubernetes/opentelemetry-demo.yaml
```

Match the version to what is deployed. Comparing a running `2.2.0` against a
`main` manifest buries real configuration drift under version skew.

### Drift is only useful if it is true

Several things spell the same value two ways, and comparing them raw produces
false drift on nearly every container. `k8srca.arch.normalise` canonicalises
them **for every source** — normalising one side only moves a false positive
rather than removing it:

| Looks like drift | Actually |
| --- | --- |
| `livenessProbe` vs `liveness_probe` | manifest camelCase vs Python client snake_case |
| `cpu: 1` vs `cpu: 1000m` | the same quantity |
| declared `requests: null` vs observed `requests: 120Mi` | the API server defaults `requests` to `limits` |

On the demo cluster these accounted for 23 of 56 reported entries. What
remains is real: an image upgrade applied without bumping the chart, four
memory limits raised in-cluster, and a collector running in a different mode
than declared.


## Answering "what changed?" (`change_history`)

`inspect_recent_changes` is the most valuable question in an investigation and
the one the agent could least often answer.

**An upstream chart repository does not answer it.** Its history records what
the *project* changed, not what was applied *here* — a deployment that tracks
upstream loosely will show commits never applied to the cluster, and miss
changes that were made locally.

Kubernetes keeps the right record itself. Each update to a Deployment creates a
ReplicaSet carrying that revision's pod template, so the ReplicaSets owned by a
Deployment are its change log:

```yaml
architecture:
  sources:
    - type: change_history
      namespaces: [default]
```

```
$ arch_query.py changes ad
ad:
  last changed   2026-04-19 (145d ago)
  revisions      2
  that change    image: demo:2.0.2-ad -> demo:2.2.0-ad; replicas: 0 -> 1
```

**"Nothing changed" is a result, not a blank.** A workload untouched for months
rules out the entire recent-regression family of hypotheses — which is far more
useful than the agent reporting it could not check. It is usually the cheapest
hypothesis to eliminate, so it belongs early in an investigation.

Two limits, both stated in the skill so the agent reads the output correctly:

- **Workload spec changes only.** A ConfigMap edit, a feature-flag toggle, or a
  change in traffic leaves no revision behind. "Unchanged" narrows the field; it
  does not close it.
- **Deployments only.** StatefulSets and DaemonSets keep history differently.

This reads through `get_replicaset_summaries`, added in **k8stools 1.2.0**.
k8srca never touches the Kubernetes API itself — see `CLAUDE.md` — so the
k8stools container must be on 1.2.0 or later; `arch build` fails with the
version it needs if the tool is absent.

Because the tool exists, the agent can also ask the question *live* during an
investigation rather than only from the build-time snapshot.
