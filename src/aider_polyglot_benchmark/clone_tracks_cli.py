from pathlib import Path
from typing import List, Optional

import typer

from aider_polyglot_benchmark.tracks import DEFAULT_DEST_DIR, clone_tracks, normalize_languages

app = typer.Typer(add_completion=False, help="Clone official Exercism language tracks into a local benchmark dataset root.")


def prompt_for_languages() -> List[str]:
    raw = typer.prompt(
        "Languages to clone from github.com/exercism (comma separated, for example: csharp,java,python)"
    )
    return normalize_languages(raw.split(","))


@app.command()
def main(
    languages: Optional[List[str]] = typer.Argument(
        None,
        help="Language track names from github.com/exercism, for example csharp java python",
    ),
    dest_dir: Path = typer.Option(
        DEFAULT_DEST_DIR,
        "--dest-dir",
        help="Directory where cloned Exercism tracks should be stored. Defaults to the root-level exercises folder.",
    ),
    update_existing: bool = typer.Option(
        False,
        "--update-existing",
        help="Run git pull --ff-only inside already cloned tracks instead of skipping them",
    ),
    repo_base_url: Optional[str] = typer.Option(
        None,
        "--repo-base-url",
        help="Override the track repo base URL, for example a local mirror or file:/// path. Defaults to EXERCISM_TRACK_REPO_BASE_URL or https://github.com/exercism",
    ),
):
    selected = normalize_languages(languages or [])
    if not selected:
        selected = prompt_for_languages()
    if not selected:
        raise typer.Exit("No languages provided")

    try:
        clone_tracks(
            selected,
            dest_dir,
            update_existing=update_existing,
            repo_base_url=repo_base_url,
        )
    except (RuntimeError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc


if __name__ == "__main__":
    app()


def run():
    app()