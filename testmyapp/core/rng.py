"""RNG reproductibles (spec §7.1 : graine par campagne, dérivée par session).

Chaque session reçoit un PRNG dérivé de
``seed(campaign, persona, scenario, index)`` : deux campagnes avec la même
graine produisent les mêmes délais, fautes de frappe et décisions.
"""
from __future__ import annotations

import hashlib
import random


def derive_seed(campaign_seed: int, *parts: object) -> int:
    payload = "|".join(str(p) for p in (campaign_seed, *parts))
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def make_rng(campaign_seed: int, *parts: object) -> random.Random:
    return random.Random(derive_seed(campaign_seed, *parts))
