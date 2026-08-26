"""tlc CLI (spec 03 §7.1.1): the whole system MUST be operable with the HTTP layer off."""

import tlc.core.determinism  # noqa: F401  (sets BLAS env; MUST import before numpy)

# isort: split

import json  # noqa: E402
import os  # noqa: E402
from pathlib import Path  # noqa: E402

import typer  # noqa: E402

app = typer.Typer(help="TLC plate readout — deterministic pipeline CLI", no_args_is_help=True)


def _svc(data_dir: str | None):
    from tlc.jobs.service import RunService

    return RunService(data_dir or os.environ.get("TLC_DATA_DIR", "data"))


@app.command()
def run(image: Path, n_lanes: int | None = typer.Option(None, help="operator lane count"),
        labels: str | None = typer.Option(None, help="comma-separated operator lane labels, e.g. S,co,R,sd"),
        data_dir: str | None = typer.Option(None), vlm_mode: str = typer.Option("off")):
    """Run one plate image; prints the run summary JSON."""
    svc = _svc(data_dir)
    lab = tuple(x.strip() for x in labels.split(",")) if labels else None
    if lab and n_lanes is None:
        n_lanes = len(lab)
    out = svc.run(image.read_bytes(), image.name, n_lanes=n_lanes, labels=lab, vlm_mode=vlm_mode)
    typer.echo(json.dumps(out, indent=2))


@app.command()
def replay(run_id: str, data_dir: str | None = typer.Option(None)):
    """Re-execute a historical run at its recorded version; reports replay_drift (E_REPLAY_DRIFT)."""
    out = _svc(data_dir).replay(run_id)
    typer.echo(json.dumps(out, indent=2))
    if out.get("replay_drift"):
        raise typer.Exit(code=2)


@app.command("list")
def list_runs(data_dir: str | None = typer.Option(None), limit: int = 20):
    for r in _svc(data_dir).repo.list_runs(limit=limit):
        typer.echo(f"{r['run_id']}  {r['status']:9s}  spots={r['n_spots_confirmed']}  {r['created_at']}  {r['original_filename']}")


@app.command()
def findings(run_id: str, data_dir: str | None = typer.Option(None)):  # noqa: B008
    """Print this run's findings (spec 02 §7.2), one line per hypothesis."""
    svc = _svc(data_dir)
    fs = svc.load_findings(run_id)
    if not fs:
        typer.echo("no findings recorded for this run")
        raise typer.Exit(1)
    for f in fs:
        typer.echo(f"{f['hypothesis_id']:5s} {f['verdict']:18s} {f['headline'][:100]}")


@app.command()
def export(run_id: str, out: str = "-", data_dir: str | None = typer.Option(None)):
    """Write the exact stored result JSON (byte-identical to the pipeline output)."""
    res = _svc(data_dir).load_result(run_id)
    if res is None:
        raise typer.Exit(code=1)
    text = Path(_svc(data_dir).repo.get_run(run_id)["result_path"]).read_text()
    if out == "-":
        typer.echo(text)
    else:
        Path(out).write_text(text)


if __name__ == "__main__":
    app()
