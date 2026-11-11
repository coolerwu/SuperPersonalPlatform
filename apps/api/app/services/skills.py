from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.config import settings

logger = logging.getLogger(__name__)

MAX_SKILL_FILE_SIZE = 10 * 1024 * 1024
MAX_SKILL_NAME_LENGTH = 64
MAX_SKILL_DESCRIPTION_LENGTH = 1024
MAX_SKILL_TOOL_NAME_LENGTH = 64
SEED_STATE_FILENAME = ".seed_state.json"
SEED_SKILLS_DIR = Path(__file__).resolve().parent.parent / "seed_skills"

SKILL_SOURCE_INITIAL = "initial"
SKILL_SOURCE_AI = "ai"
SKILL_SOURCE_MANUAL = "manual"


class SkillNotFoundError(ValueError):
    """The skill directory or its SKILL.md does not exist."""


class SkillValidationError(ValueError):
    """The skill file cannot be read or saved safely."""

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
    source: str = SKILL_SOURCE_MANUAL
    modified: bool = False


@dataclass(frozen=True)
class SkillCatalog:
    skills: tuple[SkillInfo, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillDocument:
    name: str
    directory: str
    path: str
    content: str
    warnings: tuple[str, ...] = ()
    source: str = SKILL_SOURCE_MANUAL
    modified: bool = False


def load_seed_skills(root: Path | None = None) -> dict[str, str] | None:
    """Read initialization templates from ``seed_skills/<name>/SKILL.md``.

    Returns ``None`` when the template set cannot be read completely; an
    empty dict means the directory holds no templates.
    """
    base = (root or SEED_SKILLS_DIR).expanduser()
    try:
        base = base.resolve()
        entries = sorted(base.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        logger.warning("cannot read seed skills directory %s: %s", base, exc)
        return None

    templates: dict[str, str] = {}
    for entry in entries:
        target = entry / "SKILL.md"
        try:
            if not entry.is_dir() or not target.is_file():
                continue
            templates[entry.name] = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("cannot read seed skill %s: %s", target, exc)
            return None
    return templates


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


def _parse_skill(
    directory: Path, root: Path, seed_state: dict[str, str] | None = None
) -> tuple[SkillInfo | None, str | None]:
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
    metadata_source = ""
    metadata = data.get("metadata")
    if metadata:
        if not isinstance(metadata, dict):
            warnings.append("metadata is not a mapping and was ignored")
        else:
            raw_source = metadata.get("source")
            if isinstance(raw_source, str):
                metadata_source = raw_source.strip().lower()
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

    tracked = seed_state or {}
    if directory.name in tracked:
        source = SKILL_SOURCE_INITIAL
    elif metadata_source == SKILL_SOURCE_AI:
        source = SKILL_SOURCE_AI
    else:
        source = SKILL_SOURCE_MANUAL

    tracked_hash = tracked.get(directory.name)
    modified = tracked_hash is not None and _content_hash(text) != tracked_hash

    return (
        SkillInfo(
            name=name,
            description=description,
            path=f"/skills/{directory.name}/SKILL.md",
            directory=directory.name,
            include_tools=include_tools,
            can_enable=can_enable,
            warnings=tuple(warnings),
            source=source,
            modified=modified,
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

    seed_state = _load_seed_state(base)
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
        info, error = _parse_skill(entry, base, seed_state)
        if error is not None:
            logger.warning("%s", error)
            errors.append(error)
        elif info is not None:
            skills.append(info)

    skills.sort(key=lambda item: item.name)
    return SkillCatalog(skills=tuple(skills), errors=tuple(errors))


def _skills_root() -> Path:
    try:
        return settings.skills_dir.expanduser().resolve()
    except OSError as exc:
        raise SkillValidationError(f"cannot resolve skills directory: {exc}") from exc


def _resolve_skill_directory(directory: str, base: Path | None = None) -> Path:
    raw = str(directory or "").strip()
    if not raw or raw in (".", "..") or raw.startswith(".") or "/" in raw or "\\" in raw:
        raise SkillNotFoundError("skill not found")
    root = _skills_root() if base is None else base.expanduser().resolve()
    candidate = root / raw
    if candidate.is_symlink():
        raise SkillNotFoundError("skill not found")
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise SkillNotFoundError("skill not found") from exc
    if resolved.parent != root or not resolved.is_dir():
        raise SkillNotFoundError("skill not found")
    return resolved


def _read_skill_text(directory: str) -> tuple[Path, str]:
    folder = _resolve_skill_directory(directory)
    target = folder / "SKILL.md"
    if target.is_symlink() or not target.is_file():
        raise SkillNotFoundError("skill not found")
    try:
        if target.stat().st_size > MAX_SKILL_FILE_SIZE:
            raise SkillValidationError("SKILL.md is too large")
        return target, target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillValidationError(f"cannot read SKILL.md: {exc}") from exc


def _warnings_for(name: str, description: str, directory: str) -> tuple[str, ...]:
    warnings: list[str] = []
    if name != directory:
        warnings.append(f"name '{name}' does not match directory '{directory}'")
    if not is_valid_skill_name(name):
        warnings.append("name does not follow the Agent Skills specification; it cannot be enabled")
    if len(description) > MAX_SKILL_DESCRIPTION_LENGTH:
        warnings.append(
            f"description exceeds {MAX_SKILL_DESCRIPTION_LENGTH} characters and will be truncated"
        )
    return tuple(warnings)


def _validate_skill_content(directory: str, content: str) -> tuple[str, str]:
    if not content.strip():
        raise SkillValidationError("SKILL.md is empty")
    if len(content.encode("utf-8")) > MAX_SKILL_FILE_SIZE:
        raise SkillValidationError("SKILL.md is too large")
    match = _FRONTMATTER_PATTERN.match(content)
    if match is None:
        raise SkillValidationError("SKILL.md must start with YAML frontmatter")
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillValidationError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillValidationError("frontmatter must be a mapping")
    name = data.get("name")
    description = data.get("description")
    if not isinstance(name, str) or not name.strip():
        raise SkillValidationError("frontmatter is missing 'name'")
    if not isinstance(description, str) or not description.strip():
        raise SkillValidationError("frontmatter is missing 'description'")
    name = name.strip()
    description = description.strip()
    _warnings_for(name, description, directory)
    return name, description


def read_skill(directory: str) -> SkillDocument:
    folder = _resolve_skill_directory(directory)
    _, content = _read_skill_text(directory)
    root = _skills_root()
    info, error = _parse_skill(folder, root, _load_seed_state(root))
    if info is None:
        raise SkillValidationError(error or "SKILL.md is invalid")
    return SkillDocument(
        name=info.name,
        directory=directory,
        path=info.path,
        content=content,
        warnings=info.warnings,
        source=info.source,
        modified=info.modified,
    )


def update_skill(directory: str, content: str) -> SkillDocument:
    folder = _resolve_skill_directory(directory)
    target = folder / "SKILL.md"
    if target.is_symlink() or not target.is_file():
        raise SkillNotFoundError("skill not found")

    name, description = _validate_skill_content(directory, content)
    temp = folder / f".SKILL.md.{uuid.uuid4().hex}.tmp"
    try:
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, target)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise SkillValidationError(f"cannot save SKILL.md: {exc}") from exc

    root = _skills_root()
    info, error = _parse_skill(folder, root, _load_seed_state(root))
    if info is None:
        raise SkillValidationError(error or "SKILL.md is invalid")
    return SkillDocument(
        name=name,
        directory=directory,
        path=f"/skills/{directory}/SKILL.md",
        content=content,
        warnings=_warnings_for(name, description, directory),
        source=info.source,
        modified=info.modified,
    )


def create_skill(
    name: str,
    description: str,
    instructions: str,
    *,
    include_tools: str | Iterable[str] = (),
    source: str = SKILL_SOURCE_AI,
    root: Path | None = None,
) -> SkillDocument:
    """Create a new skill file; never overwrites an existing directory."""
    skill_name = str(name or "").strip()
    if not is_valid_skill_name(skill_name):
        raise SkillValidationError(
            "invalid skill name: use lowercase letters, digits and hyphens, "
            f"at most {MAX_SKILL_NAME_LENGTH} characters"
        )
    skill_description = str(description or "").strip()
    if not skill_description:
        raise SkillValidationError("description is required")
    if len(skill_description) > MAX_SKILL_DESCRIPTION_LENGTH:
        raise SkillValidationError(
            f"description exceeds {MAX_SKILL_DESCRIPTION_LENGTH} characters"
        )
    body = str(instructions or "").strip()
    if not body:
        raise SkillValidationError("instructions are required")

    if isinstance(include_tools, str):
        raw_tools = include_tools.split()
    else:
        raw_tools = list(include_tools)
    tool_names: list[str] = []
    for raw in raw_tools:
        tool = str(raw).strip()
        if (
            not tool
            or len(tool) > MAX_SKILL_TOOL_NAME_LENGTH
            or any(char.isspace() or not char.isprintable() for char in tool)
        ):
            raise SkillValidationError(f"invalid tool name: {raw!r}")
        if tool not in tool_names:
            tool_names.append(tool)

    base = (root or settings.skills_dir).expanduser()
    try:
        base.mkdir(parents=True, exist_ok=True)
        base = base.resolve()
    except OSError as exc:
        raise SkillValidationError(f"cannot create skills directory: {exc}") from exc

    folder = base / skill_name
    if folder.is_symlink() or folder.exists():
        raise SkillValidationError(f"skill {skill_name} already exists")

    metadata: dict[str, str] = {"source": source}
    if tool_names:
        metadata["include_tools"] = " ".join(tool_names)
    frontmatter = {
        "name": skill_name,
        "description": skill_description,
        "metadata": metadata,
    }
    content = (
        f"---\n{yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()}\n"
        f"---\n\n{body}\n"
    )
    if len(content.encode("utf-8")) > MAX_SKILL_FILE_SIZE:
        raise SkillValidationError("SKILL.md is too large")
    _validate_skill_content(skill_name, content)

    try:
        folder.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise SkillValidationError(f"cannot create skill directory: {exc}") from exc

    target = folder / "SKILL.md"
    temp = folder / f".SKILL.md.{uuid.uuid4().hex}.tmp"
    try:
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, target)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        try:
            folder.rmdir()
        except OSError:
            pass
        raise SkillValidationError(f"cannot save SKILL.md: {exc}") from exc

    return SkillDocument(
        name=skill_name,
        directory=skill_name,
        path=f"/skills/{skill_name}/SKILL.md",
        content=content,
        warnings=_warnings_for(skill_name, skill_description, skill_name),
        source=source,
    )


def delete_skill(directory: str, root: Path | None = None) -> str:
    """Delete a skill directory and return its declared name.

    Deleting an initialized skill keeps its seed-state entry on purpose: the
    entry is a tombstone, so later seeds never bring the skill back.
    """
    base = _skills_root() if root is None else root.expanduser().resolve()
    folder = _resolve_skill_directory(directory, base)
    info, _ = _parse_skill(folder, base, _load_seed_state(base))
    declared_name = info.name if info is not None else directory
    try:
        shutil.rmtree(folder)
    except OSError as exc:
        raise SkillValidationError(f"cannot delete skill: {exc}") from exc
    return declared_name


def seed_default_skills(
    root: Path | None = None, templates: dict[str, str] | None = None
) -> None:
    """Initialize skills from templates once; afterwards they belong to the Agent.

    ``.seed_state.json`` records the hash last written at initialization. A
    tracked copy is updated only while its hash still matches the record.
    Edited copies are never touched again. A tracked name whose file is gone
    is a tombstone: the user deleted it, so later seeds skip it. A name that
    leaves the templates loses only its tracking; the local file stays.
    """
    base = (root or settings.skills_dir).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    if templates is None:
        loaded = load_seed_skills()
        if loaded is None:
            logger.warning("initialization templates unavailable; leaving seed state untouched")
            return
        seed_templates = loaded
    else:
        seed_templates = dict(templates)
    valid_templates: dict[str, str] = {}
    for name, content in seed_templates.items():
        if not is_valid_skill_name(name):
            logger.warning("ignoring seed skill with invalid name %r", name)
            continue
        valid_templates[name] = content
    state = _load_seed_state(base)
    changed = False

    for name, content in valid_templates.items():
        directory = base / name
        if directory.is_symlink():
            logger.warning("seed skill %s is a symlinked directory; leaving it untouched", name)
            continue
        if directory.exists() and not directory.is_dir():
            logger.warning("seed skill %s is not a directory; leaving it untouched", name)
            continue
        target = directory / "SKILL.md"
        if target.is_symlink():
            logger.warning("seed skill %s/SKILL.md is a symlink; leaving it untouched", name)
            continue

        template_hash = _content_hash(content)
        recorded = state.get(name)
        if not target.exists():
            if recorded is not None:
                logger.info("skill %s was deleted by the user; not re-adding it", name)
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_skill_text(target, content)
            except OSError as exc:
                logger.warning("cannot write seed skill %s: %s", target, exc)
                continue
            state[name] = template_hash
            changed = True
            continue

        try:
            current_hash = _content_hash(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("cannot read skill %s: %s", target, exc)
            continue

        if recorded is None:
            if current_hash == template_hash:
                state[name] = template_hash
                changed = True
            continue
        if current_hash != recorded:
            logger.info("skill %s has edits; leaving it untouched", name)
            continue
        if current_hash != template_hash:
            try:
                _write_skill_text(target, content)
            except OSError as exc:
                logger.warning("cannot update seed skill %s: %s", target, exc)
                continue
            state[name] = template_hash
            changed = True
            logger.info("updated unmodified initialization skill %s", name)

    for name in [item for item in state if item not in valid_templates]:
        state.pop(name, None)
        changed = True
        logger.info("skill %s is no longer an initialization template; tracking dropped", name)

    if changed:
        _write_seed_state(base, state)


def _write_skill_text(target: Path, content: str) -> None:
    temp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _load_seed_state(root: Path) -> dict[str, str]:
    path = root / SEED_STATE_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, dict):
        return {}
    return {
        str(name): str(value)
        for name, value in skills.items()
        if isinstance(name, str) and isinstance(value, str)
    }


def _write_seed_state(root: Path, skills: dict[str, str]) -> None:
    path = root / SEED_STATE_FILENAME
    temp = root / f"{SEED_STATE_FILENAME}.tmp"
    payload = json.dumps(
        {"version": 1, "skills": dict(sorted(skills.items()))},
        ensure_ascii=False,
        indent=2,
    )
    with temp.open("w", encoding="utf-8") as handle:
        handle.write(payload + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
