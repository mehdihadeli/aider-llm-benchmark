#!/usr/bin/env python3
from pathlib import Path
from typing import List

import typer

from aider_polyglot_benchmark.tracks import DEFAULT_DEST_DIR, cleanup_tracks


app = typer.Typer(
    add_completion=False,
    help="Remove previously cloned Exercism language tracks from the local exercises root.",
)


@app.command()
def main(
    languages: List[str] = typer.Argument(
        ...,
        help="Language track names to remove, for example csharp java python",
    ),
    dest_dir: Path = typer.Option(
        DEFAULT_DEST_DIR,
        "--dest-dir",
        help="Directory where cloned Exercism tracks are stored",
    ),
):
    try:
        summary = cleanup_tracks(languages, dest_dir)
    except ValueError as exc:
        raise typer.Exit(str(exc)) from exc

    for removed_dir in summary.removed_dirs:
        print(f"Removed track: {removed_dir}")
    for skipped_dir in summary.skipped_dirs:
        print(f"Skipped missing track: {skipped_dir}")

    if not summary.removed_dirs:
        print("No matching cloned tracks were removed.")


if __name__ == "__main__":
    app()


def run():
    app()
