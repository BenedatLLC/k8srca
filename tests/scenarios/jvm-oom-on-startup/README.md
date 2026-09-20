# jvm-oom-on-startup

**Discriminates:** handling evidence that contradicts the hypothesis.

## How the cluster was in this state

Nothing was broken on purpose. This is the OpenTelemetry demo as it ships,
running on minikube in the `default` namespace: the `ad` service is a JVM with

```yaml
resources:
  requests: {memory: 300Mi}
  limits:   {memory: 300Mi}
```

and no heap sizing (`-Xmx`, `MaxRAMPercentage`). The JVM's default sizing
exceeds the cgroup limit and the kernel kills it during startup, so the pod has
been crash-looping since the demo was deployed — 1961 restarts at capture time.

To re-make it: deploy the OTel demo (2.2.0) to minikube and wait. `ad` and
`fraud-detection` both arrive in this state unaided, which is the §4.1 argument
in miniature — real breakage produces self-consistent evidence for free, and
produces details nobody would think to write, like `reason: Error`.

## Re-recording

```bash
uv run k8srca scenario record jvm-oom-on-startup --namespace default
```

Then re-review `truth.yaml` and update `capture.captured_at`. Every disposition
in it cites a number that moves: the restart count, the container's lifetime,
and the `*_offset_seconds` fields. `k8srca scenario list` shows the scenario as
STALE until that is done (004 §6.3).

## What is in the capture that matters

| | |
|---|---|
| `ad` last_state | `exit_code: 137`, `reason: "Error"` — the designed trap |
| `ad` resources | `limits.memory == requests.memory == 300Mi` |
| container lifetime | ~173 s (offsets 371 s → 198 s before capture) |
| `ad` logs | JVM/SLF4J startup, then truncation — no OOM message, no stack trace |
| previous logs | identical to current: CrashLoopBackOff has no running instance |
| node `minikube` | `MemoryPressure: False`, 64Gi capacity — refutes node pressure |
| `fraud-detection` | independently crash-looping, same misconfiguration, not related |
