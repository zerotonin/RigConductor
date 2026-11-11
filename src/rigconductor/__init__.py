# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — rigconductor                                    ║
# ║  « from Motif recording to results on a USB disk »               ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Package root: version and the ConductorError exception.         ║
# ╚══════════════════════════════════════════════════════════════════╝
"""rigconductor — drive a Motif rig from recording to results on a USB disk."""

__version__ = "0.1.0"


class ConductorError(RuntimeError):
    """An error the operator can act on; the message is shown verbatim."""
