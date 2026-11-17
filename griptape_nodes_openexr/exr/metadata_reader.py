"""Engine-independent disk boundary for fresh EXR header metadata reads."""

from __future__ import annotations

from weakref import WeakSet

from griptape_nodes_openexr.exr.exr_io import scan_exr_header_from_path
from griptape_nodes_openexr.exr.exr_types import EXRData


class MetadataReadLifetime:
    """Invalidate a node's reads when its library is unloaded."""

    def __init__(self) -> None:
        self.active = True
        _lifetimes.add(self)


_lifetimes: WeakSet[MetadataReadLifetime] = WeakSet()


def invalidate_metadata_reads() -> None:
    """Prevent in-flight node reads from updating an unloaded library."""
    for lifetime in list(_lifetimes):
        lifetime.active = False


def read_metadata(path: str) -> EXRData:
    """Read all part headers from an absolute path, without caching or resolving it.

    This synchronous function performs disk I/O in the calling thread and touches
    no node state. The host is responsible for path resolution and offloading.
    Pixel data is not decoded; channel pixel types are reported as HALF.
    Raises ValueError for unresolved paths or files with no parts, and RuntimeError
    for file opening or header parsing failures.
    """
    return scan_exr_header_from_path(path, header_only=True)
