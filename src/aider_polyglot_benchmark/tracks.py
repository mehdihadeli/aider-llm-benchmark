import os
import stat
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEST_DIR = REPO_ROOT / "exercises"
LEGACY_DEST_DIR = REPO_ROOT / "exercism-tracks"
DEFAULT_REPO_BASE_URL = "https://github.com/exercism"


def normalize_languages(values: List[str]) -> List[str]:
    seen = set()
    languages = []
    for value in values:
        language = value.strip().lower()
        if not language or language in seen:
            continue
        seen.add(language)
        languages.append(language)
    return languages


def run_git(command: List[str], cwd: Optional[Path] = None):
    subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)


def resolve_repo_base_url(repo_base_url: Optional[str] = None) -> str:
    base_url = repo_base_url or os.environ.get("EXERCISM_TRACK_REPO_BASE_URL") or DEFAULT_REPO_BASE_URL
    return base_url.rstrip("/")


def clone_tracks(
    languages: List[str],
    dest_dir: Path,
    update_existing: bool = False,
    repo_base_url: Optional[str] = None,
):
    if shutil.which("git") is None:
        raise RuntimeError("git is required but was not found on PATH")

    selected = normalize_languages(languages)
    if not selected:
        raise ValueError("No languages provided")

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    base_url = resolve_repo_base_url(repo_base_url)

    for language in selected:
        repo_url = f"{base_url}/{language}"
        target_dir = dest_dir / language
        practice_dir = target_dir / "exercises" / "practice"

        if target_dir.exists():
            if update_existing and (target_dir / ".git").exists():
                print(f"Updating {language}: {target_dir}")
                run_git(["git", "pull", "--ff-only"], cwd=target_dir)
            else:
                print(f"Skipping existing track: {target_dir}")
            if practice_dir.exists():
                print(f"Ready: {practice_dir}")
            else:
                print(f"Warning: expected practice directory missing: {practice_dir}")
            continue

        print(f"Cloning {repo_url} -> {target_dir}")
        run_git(["git", "clone", "--depth", "1", repo_url, str(target_dir)])

        if practice_dir.exists():
            print(f"Ready: {practice_dir}")
        else:
            print(f"Warning: expected practice directory missing: {practice_dir}")

    print(f"Benchmark root: {dest_dir}")
    print("Use it with benchmark via the default exercises root or override with --exercises-dir.")


def cleanup_tracks(languages: List[str], dest_dir: Path):
    selected = normalize_languages(languages)
    if not selected:
        raise ValueError("No languages provided")

    dest_dir = Path(dest_dir)
    removed_dirs = []
    skipped_dirs = []

    for language in selected:
        target_dir = dest_dir / language
        if not target_dir.exists():
            skipped_dirs.append(target_dir)
            continue

        try:
            target_dir.resolve().relative_to(dest_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"Refusing to delete outside exercises root: {target_dir}") from exc

        if not target_dir.is_dir():
            skipped_dirs.append(target_dir)
            continue

        shutil.rmtree(target_dir, onerror=_retry_remove_with_write_permissions)
        removed_dirs.append(target_dir)

    return SimpleNamespace(removed_dirs=removed_dirs, skipped_dirs=skipped_dirs)


def _retry_remove_with_write_permissions(function, path, excinfo):
    del excinfo
    os.chmod(path, stat.S_IWRITE)
    function(path)
