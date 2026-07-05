"""``sempipe cite`` — print the BibTeX entry (the output IS the result, like --version)."""

from __future__ import annotations

import click

from sempipe import __version__

__all__ = ["cite_command"]

_BIBTEX = """\
@software{{gupta_sempipe_2026,
  author = {{Gupta, Prabal}},
  title = {{sempipe: semantic pipes for your terminal}},
  year = {{2026}},
  version = {{{version}}},
  license = {{Apache-2.0}},
  url = {{https://github.com/prabal-rje/sempipe}}
}}
"""


@click.command(name="cite")
def cite_command() -> None:
    """Print a BibTeX entry for citing sempipe."""
    click.echo(_BIBTEX.format(version=__version__), nl=False)
