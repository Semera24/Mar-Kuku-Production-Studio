"""Tests for RootPolicy read-only production containment and scratch-only output containment."""

from __future__ import annotations

from pathlib import Path
import pytest

from mk_studio.config import (
    OutputExistsError,
    PathPolicyError,
    RootPolicy,
    SourceHashMismatchError,
    SourceMissingError,
    compute_sha256,
)


def _make_policy(tmp_path: Path) -> tuple[RootPolicy, Path, Path, Path]:
    prod_root = tmp_path / "production_root"
    scratch_root = tmp_path / "repo" / "scratch" / "ch01-proof"
    outside_root = tmp_path / "outside"
    prod_root.mkdir(parents=True)
    scratch_root.mkdir(parents=True)
    outside_root.mkdir(parents=True)

    policy = RootPolicy(
        production_root=prod_root,
        output_root=scratch_root,
        extra_read_roots=(tmp_path / "repo",),
        forbidden_output_roots=(prod_root, tmp_path / "Documents" / "Palmier Pro"),
    )
    return policy, prod_root, scratch_root, outside_root


def test_resolve_read_valid_and_hash_verification(tmp_path: Path) -> None:
    policy, prod_root, _, _ = _make_policy(tmp_path)
    asset = prod_root / "Mar_Wave_v001.png"
    asset.write_bytes(b"png-bytes-test")
    digest = compute_sha256(asset)

    resolved = policy.resolve_read(asset, expected_sha256=digest)
    assert resolved == asset.resolve()


def test_resolve_read_missing_source_rejected(tmp_path: Path) -> None:
    policy, prod_root, _, _ = _make_policy(tmp_path)
    missing = prod_root / "does_not_exist.wav"
    with pytest.raises(SourceMissingError):
        policy.resolve_read(missing)


def test_resolve_read_changed_source_hash_rejected(tmp_path: Path) -> None:
    policy, prod_root, _, _ = _make_policy(tmp_path)
    asset = prod_root / "audio.wav"
    asset.write_bytes(b"original-audio")
    original_digest = compute_sha256(asset)

    # Mutate file content in test temp dir to simulate changed source
    asset.write_bytes(b"modified-audio")
    with pytest.raises(SourceHashMismatchError):
        policy.resolve_read(asset, expected_sha256=original_digest)


def test_resolve_read_parent_traversal_rejected(tmp_path: Path) -> None:
    policy, prod_root, _, outside_root = _make_policy(tmp_path)
    secret = outside_root / "secret.txt"
    secret.write_text("secret")

    traversal_path = f"{prod_root}/../outside/secret.txt"
    with pytest.raises(PathPolicyError, match="Parent traversal"):
        policy.resolve_read(traversal_path)


def test_resolve_read_symlink_escape_rejected(tmp_path: Path) -> None:
    policy, prod_root, _, outside_root = _make_policy(tmp_path)
    outside_file = outside_root / "escaped.wav"
    outside_file.write_bytes(b"outside")

    escape_link = prod_root / "link_to_outside.wav"
    escape_link.symlink_to(outside_file)

    with pytest.raises(PathPolicyError, match="Symlink escape"):
        policy.resolve_read(escape_link)


def test_resolve_output_valid_inside_scratch(tmp_path: Path) -> None:
    policy, _, scratch_root, _ = _make_policy(tmp_path)
    target = policy.resolve_output(scratch_root / "subdir" / "out.json", create_parents=True)
    assert target == (scratch_root / "subdir" / "out.json").resolve()
    assert target.parent.is_dir()


def test_resolve_output_existing_file_refused_unless_overwrite(tmp_path: Path) -> None:
    policy, _, scratch_root, _ = _make_policy(tmp_path)
    existing = scratch_root / "proof.mp4"
    existing.write_bytes(b"mp4")

    with pytest.raises(OutputExistsError):
        policy.resolve_output(existing, overwrite=False)

    allowed = policy.resolve_output(existing, overwrite=True)
    assert allowed == existing.resolve()


def test_resolve_output_production_and_outside_rejected(tmp_path: Path) -> None:
    policy, prod_root, scratch_root, outside_root = _make_policy(tmp_path)

    with pytest.raises(PathPolicyError):
        policy.resolve_output(prod_root / "illegal.wav")

    with pytest.raises(PathPolicyError):
        policy.resolve_output(outside_root / "illegal.wav")

    with pytest.raises(PathPolicyError, match="Parent traversal"):
        policy.resolve_output(f"{scratch_root}/../../production_root/illegal.wav")


def test_resolve_output_symlink_escape_rejected(tmp_path: Path) -> None:
    policy, _, scratch_root, outside_root = _make_policy(tmp_path)
    escape_dir = scratch_root / "escaped_dir"
    escape_dir.symlink_to(outside_root)

    with pytest.raises(PathPolicyError, match="Symlink escape"):
        policy.resolve_output(escape_dir / "payload.json")

    outside_file = outside_root / "target.json"
    outside_file.write_text("{}")
    file_link = scratch_root / "link.json"
    file_link.symlink_to(outside_file)

    with pytest.raises(PathPolicyError, match="Symlink escape"):
        policy.resolve_output(file_link, overwrite=True)

