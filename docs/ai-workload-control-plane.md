# AI Workload Control Plane

## Honest product claim

The current system is a **multi-agent workload orchestration control plane**. It is
not yet a distributed system: Phase 1 has no durable queue, lease/heartbeat,
idempotent claim, retry policy, or independently scalable workers.

```text
Developer (mobile/desktop)
          |
          v
Port self-service + catalog + policy
          |
          v
GitHub Actions execution adapter
          |
          v
validated container job -> audit artifact

Terraform ---- manages Port and future cloud infrastructure
Git/PR -------- reviews long-lived desired-state changes
Argo CD ------- future reconciliation after Kubernetes exists
```

## Responsibility boundaries

| Layer | Owns | Does not own |
|---|---|---|
| Port | user experience, input schema, authorization, run visibility | model credentials, worker execution |
| GitHub Actions | build/test and current remote execution adapter | product UI, durable job state |
| Terraform | reproducible Port/cloud resources | application requests as state |
| GitOps/Argo CD | reconciliation of long-lived runtime configuration | transient AI job queuing |
| relay worker | validation, execution, result metadata | identity provider or infrastructure provisioning |

## Promotion criteria: “distributed system”

Use that description only after the implementation demonstrates all of these:

1. Durable queue and job state survive process restart.
2. Atomic claim or lease prevents two workers from owning one attempt.
3. Idempotency key prevents duplicate external effects.
4. Bounded retry/backoff and dead-letter handling exist.
5. Multiple workers can run concurrently and recover expired leases.
6. Metrics expose queue depth, latency, success rate, retries, and cost.

Until then, use: **AI Workload Control Plane** or **multi-agent orchestration platform**.

