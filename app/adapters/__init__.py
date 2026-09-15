"""Adapter registry."""

from __future__ import annotations

import logging

from .base import Adapter, AdapterError, RawOffer
from .sample import SampleAdapter

log = logging.getLogger(__name__)

ALL_ADAPTERS: list[Adapter] = [SampleAdapter()]

# The network adapters need httpx; the sample adapter doesn't. Import them
# defensively so a bare environment can still run the sample feed and the
# tests instead of failing at import time on a dependency it never uses.
try:
    from .avantlink import AvantLinkAdapter
    from .cj import CJAdapter
    from .impact import ImpactAdapter
    from .shopping import ShoppingAdapter

    ALL_ADAPTERS += [
        ShoppingAdapter(),      # web-wide prices, no partnership required
        AvantLinkAdapter(),
        ImpactAdapter(),
        CJAdapter(),
    ]
except ModuleNotFoundError as exc:  # pragma: no cover
    log.warning(
        "network adapters unavailable (%s); sample feed only. "
        "Install requirements.txt to enable them.", exc.name,
    )

ADAPTERS_BY_ID = {a.id: a for a in ALL_ADAPTERS}


def configured_adapters() -> list[Adapter]:
    """Only the sources that can actually run right now."""
    return [a for a in ALL_ADAPTERS if a.is_configured()]


__all__ = [
    "Adapter", "AdapterError", "RawOffer",
    "ALL_ADAPTERS", "ADAPTERS_BY_ID", "configured_adapters",
]
