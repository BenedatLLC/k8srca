"""The facts layer (design 010 §3): observations stated by code from tool results.

Reads MCP results only, never the cluster.
"""

from .derive import DERIVATIONS, derive
from .log import FactLog, block
from .model import Fact

__all__ = ["DERIVATIONS", "Fact", "FactLog", "block", "derive"]
