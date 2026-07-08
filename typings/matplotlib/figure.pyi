from collections.abc import Mapping, Sequence
from typing import IO

from matplotlib.axes import Axes
from matplotlib.gridspec import GridSpec, SubplotSpec

class Figure:
    def __init__(
        self,
        figsize: tuple[float, float] = ...,
        dpi: float = ...,
        layout: str | None = ...,
    ) -> None: ...
    def add_subplot(self, *args: SubplotSpec) -> Axes: ...
    def add_gridspec(
        self,
        nrows: int = ...,
        ncols: int = ...,
        *,
        height_ratios: Sequence[float] | None = ...,
    ) -> GridSpec: ...
    def suptitle(
        self, t: str, *, x: float = ..., ha: str = ..., fontsize: float = ...
    ) -> object: ...
    def savefig(
        self,
        fname: str | IO[bytes],
        *,
        format: str | None = ...,
        metadata: Mapping[str, object] | None = ...,
    ) -> None: ...
