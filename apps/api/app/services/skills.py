from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.config import settings

logger = logging.getLogger(__name__)

MAX_SKILL_FILE_SIZE = 10 * 1024 * 1024
MAX_SKILL_NAME_LENGTH = 64
MAX_SKILL_DESCRIPTION_LENGTH = 1024

_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    path: str
    directory: str
    include_tools: tuple[str, ...] = ()
    can_enable: bool = True
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillCatalog:
    skills: tuple[SkillInfo, ...] = ()
    errors: tuple[str, ...] = ()


def is_valid_skill_name(name: str) -> bool:
    if not name or len(name) > MAX_SKILL_NAME_LENGTH:
        return False
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return False
    for char in name:
        if char == "-":
            continue
        if (char.isalpha() and char.islower()) or char.isdigit():
            continue
        return False
    return True


def normalize_skill_names(names: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for raw in names:
        name = str(raw).strip()
        if not is_valid_skill_name(name):
            raise ValueError(f"invalid skill name: {raw!r}")
        if name not in normalized:
            normalized.append(name)
    return normalized


def _parse_skill(directory: Path, root: Path) -> tuple[SkillInfo | None, str | None]:
    skill_md = directory / "SKILL.md"
    if not skill_md.is_file():
        return None, None
    try:
        resolved = skill_md.resolve()
        resolved.relative_to(root)
    except ValueError:
        return None, f"skipping {skill_md}: resolves outside the skills root"
    except OSError as exc:
        return None, f"skipping {skill_md}: {exc}"

    try:
        size = skill_md.stat().st_size
        if size > MAX_SKILL_FILE_SIZE:
            return None, f"skipping {skill_md}: file too large ({size} bytes)"
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"skipping {skill_md}: {exc}"

    match = _FRONTMATTER_PATTERN.match(text)
    if match is None:
        return None, f"skipping {skill_md}: no valid YAML frontmatter"
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        return None, f"skipping {skill_md}: invalid YAML ({exc})"
    if not isinstance(data, dict):
        return None, f"skipping {skill_md}: frontmatter is not a mapping"

    name_value = data.get("name")
    description_value = data.get("description")
    name = name_value.strip() if isinstance(name_value, str) else ""
    description = description_value.strip() if isinstance(description_value, str) else ""
    if not name or not description:
        return None, f"skipping {skill_md}: missing required 'name' or 'description'"

    warnings: list[str] = []
    if name != directory.name:
        warnings.append(f"name '{name}' does not match directory '{directory.name}'")
    can_enable = is_valid_skill_name(name)
    if not can_enable:
        warnings.append("name does not follow the Agent Skills specification; it cannot be enabled")
    if len(description) > MAX_SKILL_DESCRIPTION_LENGTH:
        description = description[:MAX_SKILL_DESCRIPTION_LENGTH]
        warnings.append(
            f"description exceeds {MAX_SKILL_DESCRIPTION_LENGTH} characters and was truncated"
        )

    include_tools: tuple[str, ...] = ()
    metadata = data.get("metadata")
    if metadata:
        if not isinstance(metadata, dict):
            warnings.append("metadata is not a mapping and was ignored")
        else:
            raw_tools = metadata.get("include_tools")
            if isinstance(raw_tools, list):
                warnings.append(
                    "metadata.include_tools must be a space-separated string of tool names; "
                    "the list form is ignored"
                )
            elif isinstance(raw_tools, str):
                if "," in raw_tools:
                    warnings.append(
                        "metadata.include_tools must be a space-separated string of tool names; "
                        "comma-separated values are ignored"
                    )
                else:
                    include_tools = tuple(item for item in raw_tools.split() if item)
            elif raw_tools is not None:
                warnings.append("metadata.include_tools must be a space-separated string of tool names")

    return (
        SkillInfo(
            name=name,
            description=description,
            path=f"/skills/{directory.name}/SKILL.md",
            directory=directory.name,
            include_tools=include_tools,
            can_enable=can_enable,
            warnings=tuple(warnings),
        ),
        None,
    )


def scan_skills(root: Path | None = None) -> SkillCatalog:
    base = (root or settings.skills_dir).expanduser()
    try:
        base = base.resolve()
    except OSError as exc:
        return SkillCatalog(errors=(f"cannot resolve skills directory: {exc}",))
    if not base.is_dir():
        return SkillCatalog()

    try:
        entries = sorted(base.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        return SkillCatalog(errors=(f"cannot list skills directory: {exc}",))

    skills: list[SkillInfo] = []
    errors: list[str] = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        info, error = _parse_skill(entry, base)
        if error is not None:
            logger.warning("%s", error)
            errors.append(error)
        elif info is not None:
            skills.append(info)

    skills.sort(key=lambda item: item.name)
    return SkillCatalog(skills=tuple(skills), errors=tuple(errors))
