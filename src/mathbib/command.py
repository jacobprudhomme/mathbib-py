from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Iterable, Optional

from pathlib import Path
import sys

import click

from .bibtex import BibTexHandler
from .citegen import generate_biblatex
from .record import ArchiveRecord


@click.group()
@click.version_option(prog_name="mbib (mathbib)")
@click.option("--verbose/--silent", "-v/-V", "verbose", default=True, help="Be verbose")
@click.option("--debug/--no-debug", "debug", default=False, help="Debug mode")
@click.pass_context
def cli(ctx: click.Context, verbose: bool, debug: bool) -> None:
    """MathBib is a tool to help streamline the management of BibLaTeX files associated
    with records from various mathematical repositories.
    """
    ctx.obj = {
        "verbose": verbose,
        "debug": debug,
    }


@cli.command(short_help="Generate citations from keys in file.")
@click.argument(
    "texfile",
    nargs=-1,
    type=click.Path(
        exists=True, file_okay=True, dir_okay=False, writable=True, path_type=Path
    ),
    metavar="TEXFILE",
)
@click.option(
    f"--out",
    f"out",
    type=click.Path(
        exists=True, file_okay=True, dir_okay=False, writable=True, path_type=Path
    ),
    help=f"Output file path.",
)
def generate(texfile: Iterable[Path], out: Optional[Path]):
    """Parse TEXFILE and generate bibtex entries corresponding to keys.
    If option --out is specified, write generated text to file.
    """
    bibstr = generate_biblatex(*texfile)
    if out is None:
        click.echo(bibstr, nl=False)
    else:
        out.write_text(bibstr)


@cli.group(name="get", short_help="Retrieve various records from KEY:ID pairs.")
def get_group():
    pass


@get_group.command(name="json", short_help="Get record from KEY:ID pair")
@click.argument("keyid", type=str, metavar="KEY:ID")
def json_cmd(keyid: str):
    """Generate a JSON record for KEY:ID."""
    click.echo(ArchiveRecord.from_keyid(keyid).as_json())


@get_group.command(name="bibtex", short_help="Get bibtex from KEY:ID pair")
@click.argument("key_id", type=str, metavar="KEY:ID")
def bibtex(key_id: str):
    """Generate a BibTeX record for KEY:ID."""
    bth = BibTexHandler()
    click.echo(bth.write_records((ArchiveRecord.from_keyid(key_id),)), nl=False)


@get_group.command(name="key", short_help="Get highest priority key from KEY:ID pair")
@click.argument("keyid", type=str, metavar="KEY:ID")
def key(keyid: str):
    """Generate a BibTeX record for KEY:ID."""
    click.echo(ArchiveRecord.from_keyid(keyid).priority_key())


@cli.group(name="file", short_help="Manage files associated with records.")
def file_group():
    pass


@file_group.command(name="open", short_help="Open file associated with KEY:ID pair")
@click.argument("keyid_str", type=str, metavar="KEY:ID")
def open_cmd(keyid_str: str):
    for keyid in ArchiveRecord.from_keyid(keyid_str).related_keys():
        if click.launch(str(keyid.file_path())) == 0:
            return

    # TODO: if missing file, try to download arxiv and open it instead
    click.echo("Error: Could not find associated file.", err=True)
    sys.exit(1)
