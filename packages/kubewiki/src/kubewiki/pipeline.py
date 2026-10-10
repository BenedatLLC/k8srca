"""From a collected architecture to a written wiki, with or without synthesis.

Shared by `kubewiki build` and k8srca's eval, so both build a wiki the same way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import synthesis as syn
from . import wiki
from .model import Architecture
from .providers import Provider, Usage, provider_for
from .review import Review


class CostLimit(RuntimeError):
    pass


@dataclass
class Result:
    graph: dict
    synthesised: bool = False
    cached: bool = False
    usage: Usage = field(default_factory=Usage)
    errors: list[str] = field(default_factory=list)
    estimate_usd: float | None = None


def generate(arch: Architecture, dest: Path, *, model: str | None = None,
             review: Review | None = None, cache: Path = Path(".kubewiki"),
             max_usd: float = 2.0, provider: Provider | None = None) -> Result:
    g = wiki.graph(arch)
    if review is not None:
        g = review.adjust(g)
    if model is None:
        return Result(graph=wiki.write(arch, dest, g=g, review=review))

    inp = syn.inputs(g, review)
    key = syn.digest(inp, model)
    cached_path = cache / "synthesis" / f"{key}.json"
    result = Result(graph=g, estimate_usd=syn.estimate_usd(inp, model))
    if cached_path.exists():
        saved = json.loads(cached_path.read_text())
        data, result.errors, result.cached = saved["data"], saved["errors"], True
    else:
        if result.estimate_usd is not None and result.estimate_usd > max_usd:
            raise CostLimit(f"synthesis could cost up to ${result.estimate_usd:.2f}, over the "
                            f"${max_usd:.2f} limit (--max-usd)")
        s = syn.synthesize(provider or provider_for(model), inp)
        data, result.errors, result.usage = s.data, s.errors, s.usage
        cached_path.parent.mkdir(parents=True, exist_ok=True)
        cached_path.write_text(json.dumps({"model": model, "digest": key, "data": data,
                                           "errors": s.errors}, indent=1, sort_keys=True))
    result.synthesised = True
    data = syn.apply_review(data, review)
    result.graph = wiki.write(arch, dest, g=g, synthesis=data, review=review,
                              synthesis_errors=result.errors)
    return result
