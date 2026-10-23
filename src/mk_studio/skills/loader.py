"""Modular Niche Skill and Hierarchical Memory Loader for Mar & Kuku Production Studio.

Inspired by Nous Research's Hermes Agent skills architecture (SKILL.md with YAML frontmatter).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter delimited by '---' without requiring third-party libraries."""
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    raw_yaml = parts[1].strip()
    body = parts[2].strip()

    metadata: dict[str, Any] = {}
    current_section: str | None = None
    section_dict: dict[str, Any] = {}

    for line in raw_yaml.splitlines():
        trimmed = line.rstrip()
        if not trimmed or trimmed.startswith("#"):
            continue

        # Check indentation for nested dictionaries
        indent_level = len(line) - len(line.lstrip())
        if indent_level > 0 and current_section is not None:
            subline = line.strip()
            if ":" in subline:
                k, v = subline.split(":", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                section_dict[k] = v
            continue
        else:
            if current_section and section_dict:
                metadata[current_section] = section_dict
                section_dict = {}
                current_section = None

        if ":" in trimmed:
            k, v = trimmed.split(":", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if not v:
                current_section = k
                section_dict = {}
            else:
                metadata[k] = v

    if current_section and section_dict:
        metadata[current_section] = section_dict

    return metadata, body


@dataclass
class Skill:
    """Represents an autonomous production skill / niche blueprint."""

    name: str
    title: str
    category: str
    format: str
    version: str
    metadata: dict[str, Any] = field(default_factory=dict)
    instructions_md: str = ""
    skill_dir: str = ""
    skill_file: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryManager:
    """Manages hierarchical file-based memory: user.md, memory.md, and learned rules."""

    def __init__(self, workspace_root: Path | None = None) -> None:
        self.workspace_root = workspace_root or Path(__file__).resolve().parents[3]
        self.workspace_memory_dir = self.workspace_root / "data" / "memory"
        self.global_memory_dir = Path.home() / ".studio"

        self.workspace_memory_dir.mkdir(parents=True, exist_ok=True)
        self.global_memory_dir.mkdir(parents=True, exist_ok=True)

    def get_user_profile(self) -> str:
        """Load operator preferences from global ~/.studio/user.md or workspace data/memory/user.md."""
        global_path = self.global_memory_dir / "user.md"
        ws_path = self.workspace_memory_dir / "user.md"
        if global_path.is_file():
            return global_path.read_text(encoding="utf-8")
        if ws_path.is_file():
            return ws_path.read_text(encoding="utf-8")
        return "# Studio Operator Profile\nDefault 16:9 Production Standard."

    def get_working_memory(self) -> str:
        """Load cross-session project memory and lessons learned."""
        ws_path = self.workspace_memory_dir / "memory.md"
        if ws_path.is_file():
            return ws_path.read_text(encoding="utf-8")
        return "# Studio Working Memory\nNo cross-session facts recorded yet."

    def append_learned_rule(self, rule_text: str) -> None:
        """Append a newly learned rule or operational lesson to memory.md."""
        ws_path = self.workspace_memory_dir / "memory.md"
        content = ws_path.read_text(encoding="utf-8") if ws_path.is_file() else "# Studio Working Memory\n"
        rule_entry = f"\n- *Learned Rule:* {rule_text.strip()}\n"
        ws_path.write_text(content + rule_entry, encoding="utf-8")


class SkillRegistry:
    """Discovers, loads, registers, and authors niche skills and blueprints."""

    def __init__(self, workspace_root: Path | None = None) -> None:
        self.workspace_root = workspace_root or Path(__file__).resolve().parents[3]
        self.workspace_skills_dir = self.workspace_root / "skills" / "niches"
        self.global_skills_dir = Path.home() / ".studio" / "skills" / "niches"

        self.workspace_skills_dir.mkdir(parents=True, exist_ok=True)
        self.global_skills_dir.mkdir(parents=True, exist_ok=True)

        self._active_skill_name: str = "mar-kuku-kids"
        self.memory = MemoryManager(self.workspace_root)

    @property
    def active_skill_name(self) -> str:
        return self._active_skill_name

    def set_active_skill(self, name: str) -> Skill:
        skill = self.get_skill(name)
        if not skill:
            raise ValueError(f"Skill '{name}' not found in registered skill directories")
        self._active_skill_name = name
        return skill

    def get_active_skill(self) -> Skill:
        skill = self.get_skill(self._active_skill_name)
        if not skill:
            skills = self.list_skills()
            if skills:
                self._active_skill_name = skills[0].name
                return skills[0]
            raise RuntimeError("No niche skills found in skills catalog")
        return skill

    def list_skills(self) -> list[Skill]:
        """Scan workspace and global skills directories for SKILL.md files."""
        skills_by_name: dict[str, Skill] = {}

        # 1. Check global directory first
        if self.global_skills_dir.is_dir():
            for skill_file in sorted(self.global_skills_dir.glob("*/SKILL.md")):
                skill = self._load_skill_file(skill_file)
                if skill:
                    skills_by_name[skill.name] = skill

        # 2. Check workspace directory (overrides global)
        if self.workspace_skills_dir.is_dir():
            for skill_file in sorted(self.workspace_skills_dir.glob("*/SKILL.md")):
                skill = self._load_skill_file(skill_file)
                if skill:
                    skills_by_name[skill.name] = skill

        return list(skills_by_name.values())

    def get_skill(self, name: str) -> Skill | None:
        for s in self.list_skills():
            if s.name == name:
                return s
        return None

    def _load_skill_file(self, file_path: Path) -> Skill | None:
        try:
            content = file_path.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(content)
            name = meta.get("name") or file_path.parent.name
            title = meta.get("title") or name.replace("-", " ").title()
            category = meta.get("category") or "general"
            format_str = meta.get("format") or "16:9 (1920x1080)"
            version = meta.get("version") or "1.0.0"

            return Skill(
                name=name,
                title=title,
                category=category,
                format=format_str,
                version=version,
                metadata=meta,
                instructions_md=body,
                skill_dir=str(file_path.parent),
                skill_file=str(file_path),
            )
        except Exception:
            return None

    def create_skill(
        self,
        name: str,
        title: str,
        category: str,
        instructions_md: str,
        format_standard: str = "16:9 (1920x1080 @ 30fps)",
        extra_metadata: dict[str, Any] | None = None,
    ) -> Skill:
        """Create and register a new niche skill pack."""
        safe_name = re.sub(r"[^a-z0-9_-]", "-", name.lower().strip())
        skill_dir = self.workspace_skills_dir / safe_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "scripts").mkdir(exist_ok=True)
        (skill_dir / "templates").mkdir(exist_ok=True)

        meta = {
            "name": safe_name,
            "title": title,
            "category": category,
            "format": format_standard,
            "version": "1.0.0",
        }
        if extra_metadata:
            meta.update(extra_metadata)

        frontmatter_lines = ["---"]
        for k, v in meta.items():
            if isinstance(v, dict):
                frontmatter_lines.append(f"{k}:")
                for sub_k, sub_v in v.items():
                    frontmatter_lines.append(f"  {sub_k}: \"{sub_v}\"")
            else:
                frontmatter_lines.append(f"{k}: \"{v}\"")
        frontmatter_lines.append("---")
        frontmatter_lines.append("")
        frontmatter_lines.append(instructions_md.strip())
        frontmatter_lines.append("")

        file_path = skill_dir / "SKILL.md"
        file_path.write_text("\n".join(frontmatter_lines), encoding="utf-8")

        new_skill = self._load_skill_file(file_path)
        if not new_skill:
            raise RuntimeError(f"Failed to load newly created skill at {file_path}")
        return new_skill

    def learn_new_niche_skill(self, prompt: str) -> Skill:
        """Hermes /learn loop: Synthesizes a new 16:9 niche skill blueprint from user natural language instructions."""
        slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()[:32]).strip("-") or "custom-niche"
        title = prompt.strip().split(".")[0][:45]
        body = f"""# {title} Blueprint (16:9 YouTube Standard)

## Mission & Concept
{prompt.strip()}

## 16:9 Video Production Specifications
- Canvas: 1920x1080 (16:9 Widescreen @ 30fps).
- High Retention Hook: 0:00 - 0:10 seconds.
- Voiceover Delivery: Clear, engaging cadence (~140 wpm).
- Background Music: Ducked at -18dB under vocal narration.
- Palmier Tracks: Track 1 (Visual Background), Track 2 (Subject/B-Roll), Audio 1 (Dialogue), Audio 2 (BGM).
"""
        return self.create_skill(
            name=slug,
            title=title,
            category="custom_niche",
            instructions_md=body,
            format_standard="16:9 (1920x1080 @ 30fps)",
        )
