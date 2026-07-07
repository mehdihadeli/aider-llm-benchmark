from pathlib import Path
from tempfile import TemporaryDirectory

from aider_polyglot_benchmark.tracks import cleanup_tracks, normalize_languages


def test_normalize_languages_deduplicates_and_trims():
    assert normalize_languages([" CSharp ", "java", "csharp", " "]) == ["csharp", "java"]


def test_cleanup_tracks_removes_requested_languages_only():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        csharp_dir = root / "csharp"
        java_dir = root / "java"
        csharp_dir.mkdir()
        java_dir.mkdir()

        summary = cleanup_tracks(["csharp", "python"], root)

        assert not csharp_dir.exists()
        assert java_dir.exists()
        assert [path.name for path in summary.removed_dirs] == ["csharp"]
        assert [path.name for path in summary.skipped_dirs] == ["python"]