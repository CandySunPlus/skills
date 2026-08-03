#!/usr/bin/env python3
"""Guard read-only sources and validate generated Obsidian Markdown notes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable


REQUIRED_PROPERTIES = {"aliases", "类型", "项目", "状态", "更新日期", "tags"}
WIKILINK_RE = re.compile(r"!?\[\[([^\]]+)\]\]")
LOCAL_MD_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\((?!https?://|mailto:|#)([^)]+)\)")
PROPERTY_RE = re.compile(r"^([^\s:#][^:]*):(?:\s|$)")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_source_files(root: Path) -> Iterable[tuple[str, Path]]:
    root = root.absolute()
    if root.is_file():
        yield root.name, root
        return
    if not root.is_dir():
        raise FileNotFoundError(f"source does not exist or is not a directory: {root}")

    seen_dirs: set[Path] = set()
    for directory, dirnames, filenames in os.walk(root, followlinks=True):
        real_directory = Path(directory).resolve()
        if real_directory in seen_dirs:
            dirnames[:] = []
            continue
        seen_dirs.add(real_directory)
        dirnames[:] = sorted(dirnames)
        for filename in sorted(filenames):
            path = Path(directory, filename)
            if path.is_file():
                yield path.relative_to(root).as_posix(), path


def build_manifest(sources: list[Path]) -> dict[str, object]:
    files: dict[str, dict[str, object]] = {}
    for index, source in enumerate(sources):
        for relative, path in iter_source_files(source):
            key = f"{index}:{relative}"
            files[key] = {
                "size": path.stat().st_size,
                "sha256": sha256(path),
            }
    return {
        "version": 1,
        "sources": [str(path.absolute()) for path in sources],
        "files": files,
    }


def snapshot(args: argparse.Namespace) -> int:
    sources = [Path(value) for value in args.source]
    manifest = build_manifest(sources)
    output = Path(args.manifest).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"SNAPSHOT_OK sources={len(sources)} files={len(manifest['files'])} manifest={output}")
    return 0


def load_manifest(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("sources"), list):
        raise ValueError(f"unsupported source manifest: {path}")
    return data


def verify_sources(manifest: dict[str, object]) -> list[str]:
    sources = [Path(value) for value in manifest["sources"]]
    current = build_manifest(sources)
    expected_files = manifest.get("files", {})
    current_files = current.get("files", {})
    errors: list[str] = []
    for key in sorted(set(expected_files) | set(current_files)):
        if key not in expected_files:
            errors.append(f"source added: {key}")
        elif key not in current_files:
            errors.append(f"source removed: {key}")
        elif expected_files[key] != current_files[key]:
            errors.append(f"source modified: {key}")
    return errors


def overlaps(left: Path, right: Path) -> bool:
    left_real = left.resolve()
    right_real = right.resolve()
    return left_real == right_real or left_real in right_real.parents or right_real in left_real.parents


def frontmatter_properties(text: str) -> tuple[set[str], str | None]:
    if not text.startswith("---\n"):
        return set(), "missing YAML frontmatter"
    end = text.find("\n---\n", 4)
    if end < 0:
        return set(), "unclosed YAML frontmatter"
    frontmatter = text[4:end]
    properties = {
        match.group(1).strip()
        for line in frontmatter.splitlines()
        if (match := PROPERTY_RE.match(line))
    }
    missing = REQUIRED_PROPERTIES - properties
    if missing:
        return properties, "missing properties: " + ", ".join(sorted(missing))
    return properties, None


def resolve_wikilink(vault: Path, raw: str) -> bool:
    target = wikilink_target(raw)
    if not target:
        return True
    candidate = vault / target
    if candidate.exists() or candidate.with_suffix(".md").exists():
        return True
    if "/" not in target:
        matches = list(vault.rglob(target + ".md"))
        return len(matches) == 1
    return False


def wikilink_target(raw: str) -> str:
    target = raw.split("|", 1)[0].removesuffix("\\")
    return target.split("#", 1)[0].strip()


def validate_note(
    vault: Path,
    note: Path,
    forbid_local_links: bool,
    source_link_prefixes: tuple[str, ...],
) -> list[str]:
    text = note.read_text(encoding="utf-8")
    lines = text.splitlines()
    errors: list[str] = []
    _, frontmatter_error = frontmatter_properties(text)
    if frontmatter_error:
        errors.append(frontmatter_error)
    if "> [!abstract] 文档职责" not in text:
        errors.append("missing responsibility Callout")
    if sum(1 for line in text.splitlines() if line.startswith("```")) % 2:
        errors.append("unbalanced fenced code block")
    for raw in WIKILINK_RE.findall(text):
        if not resolve_wikilink(vault, raw):
            errors.append(f"unresolved WikiLink: [[{raw}]]")
    if forbid_local_links:
        for target in LOCAL_MD_LINK_RE.findall(text):
            errors.append(f"local Markdown link must be a WikiLink: {target}")
    source_heading_lines = [
        line_number
        for line_number, line in enumerate(lines, start=1)
        if line == "## 来源"
    ]
    source_heading_line = source_heading_lines[0] if source_heading_lines else None
    if len(source_heading_lines) > 1:
        errors.append("multiple ## 来源 sections")
    if source_heading_line is not None:
        for line_number, line in enumerate(lines[source_heading_line:], start=source_heading_line + 1):
            if line.startswith("## "):
                errors.append(f"line {line_number}: ## 来源 must be the final H2 section")
                break

    for line_number, line in enumerate(lines, start=1):
        if line.startswith("|") and re.search(r"\[\[[^\]]*(?<!\\)\|[^\]]+\]\]", line):
            errors.append(f"line {line_number}: unescaped WikiLink alias pipe inside table")
        if line.rstrip() != line:
            errors.append(f"line {line_number}: trailing whitespace")
        for raw in WIKILINK_RE.findall(line):
            target = wikilink_target(raw)
            is_source_link = any(
                target == prefix or target.startswith(prefix + "/")
                for prefix in source_link_prefixes
            )
            if is_source_link and (source_heading_line is None or line_number <= source_heading_line):
                errors.append(
                    f"line {line_number}: source WikiLink must appear only under final ## 来源: [[{raw}]]"
                )
    return errors


def validate(args: argparse.Namespace) -> int:
    vault = Path(args.vault_root).resolve()
    target = Path(args.target).resolve()
    manifest_path = Path(args.manifest).resolve()
    manifest = load_manifest(manifest_path)
    errors = verify_sources(manifest)
    source_link_prefixes: list[str] = []

    for source_value in manifest["sources"]:
        source_path = Path(source_value)
        if overlaps(target, source_path):
            errors.append(f"target overlaps source: {source_value}")
        try:
            source_link_prefixes.append(source_path.absolute().relative_to(vault).as_posix())
        except ValueError:
            pass

    notes = sorted(target.rglob("*.md")) if target.is_dir() else []
    if not notes:
        errors.append(f"target contains no Markdown notes: {target}")
    for note in notes:
        for error in validate_note(
            vault,
            note,
            args.forbid_local_markdown_links,
            tuple(source_link_prefixes),
        ):
            errors.append(f"{note.relative_to(target)}: {error}")

    if errors:
        for error in errors:
            print(f"ERROR {error}")
        print(f"VALIDATION_FAILED errors={len(errors)}")
        return 1
    print(f"VALIDATION_OK notes={len(notes)} sources_unchanged=true")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    subparsers = root.add_subparsers(dest="command", required=True)

    snap = subparsers.add_parser("snapshot", help="hash source files before generation")
    snap.add_argument("--source", action="append", required=True, help="read-only source path; repeatable")
    snap.add_argument("--manifest", required=True, help="output JSON manifest path")
    snap.set_defaults(func=snapshot)

    check = subparsers.add_parser("validate", help="verify sources and generated Obsidian notes")
    check.add_argument("--vault-root", required=True)
    check.add_argument("--target", required=True)
    check.add_argument("--manifest", required=True)
    check.add_argument("--forbid-local-markdown-links", action="store_true")
    check.set_defaults(func=validate)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
