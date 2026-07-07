def test_clone_tracks_help_command_runs(run_cli):
    result = run_cli("clone-exercism-tracks", ["--help"])

    assert result.returncode == 0
    assert "--update-existing" in result.stdout
    assert "--repo-base-url" in result.stdout


def test_cleanup_tracks_help_command_runs(run_cli):
    result = run_cli("cleanup-exercism-tracks", ["--help"])

    assert result.returncode == 0
    assert "Language track names to remove" in result.stdout
    assert "--dest-dir" in result.stdout


def test_clone_tracks_command_clones_and_updates_from_local_repo(tmp_path, run_cli, init_track_repo, add_exercise_and_commit):
    source_parent = tmp_path / "source"
    dest_dir = tmp_path / "dest"
    init_track_repo(source_parent, "csharp", ["alpha"])

    base_url = source_parent.as_uri()
    first = run_cli(
        "clone-exercism-tracks",
        [
            "csharp",
            "--dest-dir",
            str(dest_dir),
            "--repo-base-url",
            base_url,
        ]
    )

    assert first.returncode == 0, first.stdout + first.stderr
    assert (dest_dir / "csharp" / "exercises" / "practice" / "alpha").exists()

    add_exercise_and_commit(source_parent, "csharp", "beta", "add beta")

    second = run_cli(
        "clone-exercism-tracks",
        [
            "csharp",
            "--dest-dir",
            str(dest_dir),
            "--repo-base-url",
            base_url,
            "--update-existing",
        ]
    )

    assert second.returncode == 0, second.stdout + second.stderr
    assert (dest_dir / "csharp" / "exercises" / "practice" / "beta").exists()


def test_cleanup_tracks_command_removes_requested_language(tmp_path, run_cli, init_track_repo):
    source_parent = tmp_path / "source"
    dest_dir = tmp_path / "dest"
    init_track_repo(source_parent, "csharp", ["alpha"])

    clone_result = run_cli(
        "clone-exercism-tracks",
        [
            "csharp",
            "--dest-dir",
            str(dest_dir),
            "--repo-base-url",
            source_parent.as_uri(),
        ]
    )
    assert clone_result.returncode == 0, clone_result.stdout + clone_result.stderr

    cleanup_result = run_cli(
        "cleanup-exercism-tracks",
        [
            "csharp",
            "--dest-dir",
            str(dest_dir),
        ]
    )

    assert cleanup_result.returncode == 0, cleanup_result.stdout + cleanup_result.stderr
    assert not (dest_dir / "csharp").exists()


def test_clone_tracks_command_clones_multiple_languages_at_once(tmp_path, run_cli, init_track_repo):
    source_parent = tmp_path / "source"
    dest_dir = tmp_path / "dest"
    init_track_repo(source_parent, "csharp", ["alpha"])
    init_track_repo(source_parent, "java", ["hello"])
    init_track_repo(source_parent, "go", ["twofer"])

    clone_result = run_cli(
        "clone-exercism-tracks",
        [
            "csharp",
            "java",
            "go",
            "--dest-dir",
            str(dest_dir),
            "--repo-base-url",
            source_parent.as_uri(),
        ]
    )
    assert clone_result.returncode == 0, clone_result.stdout + clone_result.stderr

    assert (dest_dir / "csharp" / "exercises" / "practice" / "alpha").exists()
    assert (dest_dir / "java" / "exercises" / "practice" / "hello").exists()
    assert (dest_dir / "go" / "exercises" / "practice" / "twofer").exists()

    cleanup_result = run_cli(
        "cleanup-exercism-tracks",
        [
            "csharp",
            "go",
            "--dest-dir",
            str(dest_dir),
        ]
    )
    assert cleanup_result.returncode == 0, cleanup_result.stdout + cleanup_result.stderr

    assert not (dest_dir / "csharp").exists()
    assert not (dest_dir / "go").exists()
    assert (dest_dir / "java").exists()