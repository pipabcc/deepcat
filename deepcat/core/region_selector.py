from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Region:
    left: int
    top: int
    width: int
    height: int

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.width, self.height)


def normalize_region(region: Optional[tuple[int, int, int, int] | Region]) -> Optional[tuple[int, int, int, int]]:
    if region is None:
        return None
    if isinstance(region, Region):
        region = region.as_tuple()
    left, top, width, height = region
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise ValueError("region 的 width/height 必须 > 0")
    return (int(left), int(top), width, height)
