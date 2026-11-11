# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — profiles                                        ║
# ║  « what differs between organisms »                              ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Registry of organism profiles.                                  ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Organism profiles: everything that differs between Drosophila arenas and tadpole plates."""

from __future__ import annotations

from rigconductor.profiles.base import Profile, SessionContext

PROFILE_NAMES = ("drosophila_triad", "tadpole_24well")


def make_profile(name: str) -> Profile:
    if name == "drosophila_triad":
        from rigconductor.profiles.drosophila import DrosophilaTriad

        return DrosophilaTriad()
    if name == "tadpole_24well":
        from rigconductor.profiles.tadpole import Tadpole24Well

        return Tadpole24Well()
    raise ValueError(f"unknown profile {name!r}; choose from {PROFILE_NAMES}")


__all__ = ["PROFILE_NAMES", "Profile", "SessionContext", "make_profile"]
