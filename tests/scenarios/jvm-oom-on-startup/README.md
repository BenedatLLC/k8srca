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
been crash-looping since the demo was deployed — 2846 restarts at capture time.

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
| `ad` state at capture | Running, 1/1 ready, 46 s into a life — no readiness probe, so ready means only "started" |
| previous life | `ran_for` 5 s (offsets 356 s → 351 s before capture); next start 305 s later (5m back-off) |
| restart cadence | `Pulled` count 601 over ~2d10h: one restart per ~5.8 min. `Created` (595) stops updating 29 min before capture — kubelet event rate limiting |
| `ad` logs | current and previous both stop after the same 7 lines (~2.4 s of startup) — no OOM message, no stack trace |
| node `minikube` | `MemoryPressure: False`, 64Gi capacity — refutes node pressure |
| `fraud-detection` | 300Mi, 3216 restarts, 2 s lives, in back-off — same misconfiguration, independent service |
| `accounting` | 120Mi, 512 restarts, last life ~11 min, ready between kills — memory-pressured, a different severity |
| every container | current life began ~2d10h ago, previous lasted ~34 h — the cluster starting, not a failure. Event records begin at the same moment |
| `load-generator` | 15 restarts, last exit 137 after ~3 h (1500Mi) — not examined by the truth |
