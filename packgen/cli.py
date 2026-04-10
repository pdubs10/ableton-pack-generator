"""packgen CLI."""

import sys
from pathlib import Path

import click

from . import adg, nksf, pack
from .config import load as load_config
from .scan import scan_cmd


@click.group()
def cli():
    """Generate Ableton Live Custom Packs from VST3 NKSF presets."""


@cli.command()
@click.option("--config", "-c", required=True, type=click.Path(exists=True, path_type=Path), help="Path to plugin.toml")
@click.option("--no-params", is_flag=True, default=False, help="Omit parameter mappings (emit empty <ParameterSettings />)")
@click.option("--dry-run", is_flag=True, default=False, help="Print what would be generated without writing files")
def generate(config: Path, no_params: bool, dry_run: bool):
    """Generate a full Custom Pack from a directory of NKSF files."""
    plugin = load_config(config)

    missing = [d for d in plugin.nksf_dirs if not d.is_dir()]
    if missing:
        click.echo(f"Warning: nksf_dir does not exist: {missing}. Ignoring...")
        plugin.nksf_dirs = [d for d in plugin.nksf_dirs if d.is_dir()]

    if dry_run:
        click.echo(f"Dry run — pack: {plugin.pack_display_name}")
    else:
        click.echo(f"Generating pack: {plugin.pack_display_name}")

    output = pack.assemble(
        plugin=plugin,
        output_dir=plugin.pack_dir,
        include_params=not no_params,
        dry_run=dry_run,
    )

    if not dry_run:
        click.echo(f"Written: {output}")


@cli.command()
@click.option("--config", "-c", required=True, type=click.Path(exists=True, path_type=Path), help="Path to plugin.toml")
@click.option("--nksf", "nksf_path", required=True, type=click.Path(exists=True, path_type=Path), help="Path to a single .nksf file")
@click.option("--out", "-o", required=True, type=click.Path(path_type=Path), help="Output .adg path")
@click.option("--no-params", is_flag=True, default=False, help="Omit parameter mappings")
def adg_cmd(config: Path, nksf_path: Path, out: Path, no_params: bool):
    """Generate a single ADG preset from one NKSF file."""
    plugin = load_config(config)
    preset = nksf.parse(nksf_path)
    params = nksf.extract_params(preset.nica)
    adg_bytes = adg.build(preset.pchk, params, plugin, include_params=not no_params)
    out.write_bytes(adg_bytes)
    click.echo(f"Written: {out}  ({len(adg_bytes)} bytes, {len(params)} params)")


# Register adg subcommand under its natural name
cli.add_command(adg_cmd, name="adg")
cli.add_command(scan_cmd, name="scan")
