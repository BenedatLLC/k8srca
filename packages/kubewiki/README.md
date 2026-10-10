# kubewiki — deployment knowledge graph generator

Builds a knowledge graph of a Kubernetes deployment, as a small wiki of linked
Markdown pages any LLM agent can read: what each component is, how the
components connect, and what a failure of one means for the others.

**Status:** early. kubewiki currently builds the architecture skill used by
[k8srca](../../README.md), and is being redesigned into the wiki described in
[docs/design.md](docs/design.md). It is not yet published or usable on its own.

kubewiki reads a cluster only through a [k8stools](https://github.com/BenedatLLC/k8stools)
MCP server: it never imports a Kubernetes client or runs `kubectl`.
