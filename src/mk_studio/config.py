"""Path containment policy and studio environment configuration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PRODUCTION_ROOT = Path("/Users/yonassayfu/Desktop/DoroNesh_2D_Animation")
DEFAULT_REPO_ROOT = Path("/Users/yonassayfu/Projects/mar-kuku-production-studio")
DEFAULT_SCRATCH_OUTPUT_ROOT = DEFAULT_REPO_ROOT / "scratch" / "ch01-proof"
DEFAULT_PALMIER_DOCUMENTS_DIR = Path.home() / "Documents" / "Palmier Pro"
DEFAULT_PALMIER_MCP_URL = "http://127.0.0.1:19789/mcp"
DEFAULT_FFMPEG_BIN = Path("/opt/homebrew/bin/ffmpeg")
DEFAULT_FFPROBE_BIN = Path("/opt/homebrew/bin/ffprobe")


class PathPolicyError(PermissionError):
    """Raised when a path violates RootPolicy read or output containment."""


class SourceMissingError(PathPolicyError, FileNotFoundError):
    """Raised when a required read source does not exist."""


class SourceHashMismatchError(PathPolicyError):
    """Raised when a read source's SHA-256 does not match the pinned hash."""


class OutputExistsError(PathPolicyError, FileExistsError):
    """Raised when an output target already exists and overwrite is False."""


def compute_sha256(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute hex SHA-256 digest of a file."""
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


@dataclass(frozen=True)
class RootPolicy:
    """Enforces read-only production root and scratch-only output containment."""

    production_root: Path = DEFAULT_PRODUCTION_ROOT
    output_root: Path = DEFAULT_SCRATCH_OUTPUT_ROOT
    extra_read_roots: tuple[Path, ...] = field(default_factory=lambda: (DEFAULT_REPO_ROOT,))
    forbidden_output_roots: tuple[Path, ...] = field(
        default_factory=lambda: (DEFAULT_PRODUCTION_ROOT, DEFAULT_PALMIER_DOCUMENTS_DIR)
    )

    @property
    def production_root_resolved(self) -> Path:
        return self.production_root.expanduser().resolve(strict=False)

    @property
    def output_root_resolved(self) -> Path:
        return self.output_root.expanduser().resolve(strict=False)

    @property
    def allowed_read_roots(self) -> tuple[Path, ...]:
        roots = [self.production_root_resolved, self.output_root_resolved]
        for extra in self.extra_read_roots:
            resolved = extra.expanduser().resolve(strict=False)
            if resolved not in roots:
                roots.append(resolved)
        return tuple(roots)

    @property
    def resolved_forbidden_output_roots(self) -> tuple[Path, ...]:
        roots = [self.production_root_resolved]
        for extra in self.forbidden_output_roots:
            resolved = extra.expanduser().resolve(strict=False)
            if resolved not in roots:
                roots.append(resolved)
        return tuple(roots)

    def _check_no_escaping_symlinks(self, candidate: Path, allowed_roots: tuple[Path, ...]) -> None:
        """Ensure no symlink along candidate's path points outside allowed_roots."""
        current = Path(candidate.anchor) if candidate.is_absolute() else Path.cwd()
        parts = candidate.parts[1:] if candidate.is_absolute() else candidate.parts
        for part in parts:
            if part in ("", "."):
                continue
            current = current / part
            if current.is_symlink():
                target_resolved = current.resolve(strict=False)
                if not any(target_resolved.is_relative_to(root) for root in allowed_roots):
                    raise PathPolicyError(
                        f"Symlink escape rejected: {current} -> {target_resolved} is outside allowed roots {allowed_roots}"
                    )

    def resolve_read(self, path: str | Path, *, expected_sha256: str | None = None) -> Path:
        """Resolve a read path inside allowed read roots and optionally verify its SHA-256."""
        raw = Path(path).expanduser()
        if not raw.is_absolute():
            raise PathPolicyError(f"Read path must be absolute: {path}")

        if ".." in raw.parts:
            raise PathPolicyError(f"Parent traversal ('..') is not allowed in read path: {path}")

        allowed = self.allowed_read_roots
        self._check_no_escaping_symlinks(raw, allowed)

        try:
            resolved = raw.resolve(strict=True)
        except FileNotFoundError as exc:
            raise SourceMissingError(f"Required source file does not exist: {raw}") from exc

        if not any(resolved.is_relative_to(root) for root in allowed):
            raise PathPolicyError(
                f"Read path {resolved} is outside allowed read roots: {[str(r) for r in allowed]}"
            )

        # If the lexical path was under production_root, ensure symlinks didn't jump to another root
        if raw.is_relative_to(self.production_root.expanduser()) and not resolved.is_relative_to(
            self.production_root_resolved
        ):
            raise PathPolicyError(
                f"Production path {raw} resolved outside production root: {resolved}"
            )

        if expected_sha256 is not None:
            actual_sha256 = compute_sha256(resolved)
            if actual_sha256.lower() != expected_sha256.lower():
                raise SourceHashMismatchError(
                    f"SHA-256 mismatch for {resolved}: expected {expected_sha256.lower()}, got {actual_sha256.lower()}"
                )

        return resolved

    def resolve_output(
        self,
        path: str | Path,
        *,
        overwrite: bool = False,
        create_parents: bool = False,
    ) -> Path:
        """Resolve an output path strictly inside output_root and never in production_root."""
        raw = Path(path).expanduser()
        if not raw.is_absolute():
            raw = self.output_root / raw

        if ".." in raw.parts:
            raise PathPolicyError(f"Parent traversal ('..') is not allowed in output path: {path}")

        out_root = self.output_root_resolved
        self._check_no_escaping_symlinks(raw, (out_root,))

        if raw.is_symlink():
            raise PathPolicyError(f"Refusing to write through symlink output target: {raw}")

        resolved = raw.resolve(strict=False)

        for forbidden in self.resolved_forbidden_output_roots:
            if resolved.is_relative_to(forbidden) and not out_root.is_relative_to(forbidden):
                raise PathPolicyError(
                    f"Output path {resolved} is inside forbidden root {forbidden}"
                )

        if not resolved.is_relative_to(out_root):
            raise PathPolicyError(
                f"Output path {resolved} escapes scratch output root {out_root}"
            )

        if resolved == out_root:
            raise PathPolicyError(f"Output path must be a child inside output root {out_root}")

        if not overwrite and (resolved.exists() or resolved.is_symlink()):
            raise OutputExistsError(
                f"Output path already exists and overwrite=False: {resolved}"
            )

        if create_parents:
            resolved.parent.mkdir(parents=True, exist_ok=True)

        return resolved
