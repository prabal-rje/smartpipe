from collections.abc import Mapping, Sequence

class BarContainer: ...

class Spine:
    def set_visible(self, visible: bool) -> None: ...

class Axis:
    def set_major_locator(self, locator: object) -> None: ...

class Axes:
    xaxis: Axis
    yaxis: Axis
    spines: Mapping[str, Spine]
    def barh(
        self,
        y: Sequence[float],
        width: Sequence[float],
        *,
        color: str = ...,
        edgecolor: str = ...,
        linewidth: float = ...,
        height: float = ...,
    ) -> BarContainer: ...
    def bar(
        self,
        x: Sequence[float],
        height: Sequence[float],
        *,
        color: str = ...,
        edgecolor: str = ...,
        linewidth: float = ...,
        width: float = ...,
    ) -> BarContainer: ...
    def bar_label(
        self,
        container: BarContainer,
        labels: Sequence[str] | None = ...,
        *,
        padding: float = ...,
        fontsize: float = ...,
    ) -> object: ...
    def set_xticks(self, ticks: Sequence[float], labels: Sequence[str] | None = ...) -> object: ...
    def set_yticks(self, ticks: Sequence[float], labels: Sequence[str] | None = ...) -> object: ...
    def invert_yaxis(self) -> None: ...
    def set_xlim(self, left: float = ..., right: float = ...) -> object: ...
    def set_title(
        self, label: str, *, loc: str = ..., fontsize: float = ..., pad: float = ...
    ) -> object: ...
    def grid(
        self,
        visible: bool | None = ...,
        which: str = ...,
        axis: str = ...,
        *,
        color: str = ...,
        linewidth: float = ...,
    ) -> None: ...
    def set_axisbelow(self, b: bool) -> None: ...
    def tick_params(self, axis: str = ..., *, length: float = ...) -> None: ...
