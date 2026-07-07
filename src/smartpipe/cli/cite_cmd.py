"""``smartpipe cite`` — print the BibTeX entry (the output IS the result, like --version)."""

from __future__ import annotations

import click

from smartpipe import __version__

__all__ = ["cite_command"]

_BIBTEX = """\
@software{{gupta_smartpipe_2026,
  author = {{Gupta, Prabal}},
  title = {{smartpipe: semantic pipes for your terminal}},
  year = {{2026}},
  version = {{{version}}},
  license = {{Apache-2.0}},
  url = {{https://github.com/prabal-rje/smartpipe}}
}}
"""


@click.command(name="cite")
def cite_command() -> None:
    """Print a BibTeX entry for citing smartpipe."""
    click.echo(_BIBTEX.format(version=__version__), nl=False)
