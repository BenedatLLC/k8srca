# Vendored: the RCA knowledge base

`kubernetes_rca_knowledge_base_v2.json` is the knowledge base as we received
it: 81 alert records with their candidate causes and evidence. It is the
source `k8srca kb build` normalises into `skills/k8s-rca/knowledge_base.json`
(design 002 §8; `src/k8srca/kb/build.py`).

**Do not edit it.** What we write goes in `docs/rca/discriminators.yaml`,
kept separate so what we received stays distinguishable from what we
authored. A new version of the dataset replaces this file whole, and
`k8srca kb build` then shows what moved.
