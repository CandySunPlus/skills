---
name: structure-obsidian-docs
description: Extract information from one or more specified source directories and reorganize it into a single-responsibility Obsidian project-document set without modifying the sources. Use when Codex is asked to 整理/提取/重构指定目录中的 Markdown、ADR、research、requirements、notes, or symlinked documentation into an Obsidian vault with Properties, full-path WikiLinks, Callouts, Mermaid, task lists, traceability, requirements, technical design, acceptance criteria, and a project MOC.
---

# Structure Obsidian Docs

Turn read-only source material into a navigable Obsidian project-document set. Preserve source facts, make conflicts visible, and keep each output note responsible for one reason to change.

## Inputs

Resolve these before writing:

- **Vault root**: the directory containing `.obsidian/`.
- **Source directories**: one or more user-specified directories; they may contain symlinks.
- **Target directory**: a separate directory inside the vault.
- **Project name** and desired document language.

If the target overlaps a source, stop and ask for a separate target. Treat every source and symlink target as read-only unless the user explicitly changes scope.

## Workflow

### 1. Guard the sources

Inspect source and target state before edits. Preserve unrelated dirty files.

Create a temporary hash manifest with the bundled guard:

```bash
python3 <skill-root>/scripts/obsidian_docs_guard.py snapshot \
  --source <source-dir> [--source <another-source-dir>] \
  --manifest <absolute-temp-path>/source-manifest.json
```

Also record `git status --short` for source and target paths when they are in Git. Do not use Git status alone when a source is a symlink to another repository; the hash manifest protects the resolved content.

### 2. Inventory before synthesizing

Use `rg --files`, `find -L`, headings, and file sizes to inventory the corpus. Read:

1. accepted decisions and domain terminology;
2. requirements and current contracts;
3. milestone/pending documents;
4. research summaries, conclusions, risks, and only the detailed sections needed for implementation.

Distinguish four fact types:

- **Requirement** — externally observable behavior or constraint.
- **Decision** — accepted design and rationale, normally owned by an ADR.
- **Recommendation** — time-sensitive research conclusion, not yet an accepted decision.
- **Open item** — unresolved, unverified, or deferred work.

Never silently reconcile contradictions. Put them in an Obsidian warning Callout and create a tracked open item. Prefer the corpus's declared authority order; otherwise document the ambiguity.

### 3. Design the note topology

Create the smallest useful set. A typical project uses:

```text
<target>/
├── README.md                         # project MOC only
├── 需求文档/
│   ├── 01-项目目标与范围.md          # why, boundary, non-goals
│   ├── 02-功能需求.md                # observable behavior with stable IDs
│   ├── 03-质量属性与约束.md          # cross-cutting constraints
│   └── 04-验收标准.md                # requirement-to-test traceability
└── 技术文档/
    ├── 01-总体架构.md                # modules, interfaces, invariants
    ├── <one note per technical seam>
    └── 99-待决事项与路线图.md        # unresolved work only
```

Adapt names and count to the corpus. Do not split merely for symmetry. Give each note a “负责/不负责” statement. Define a fact once in its owner note; elsewhere use a WikiLink.

### 4. Write with native Obsidian syntax

First inspect `.obsidian/core-plugins.json` and `.obsidian/community-plugins.json`. Use only installed features. Do not emit Dataview queries unless Dataview is installed.

Start every note with Properties:

```yaml
---
aliases:
  - <human-friendly alias>
类型: 需求文档
项目: <project>
模块: <single responsibility>
状态: 草案
更新日期: YYYY-MM-DD
上级: "[[<vault-relative-path-without-extension>|<MOC label>]]"
tags:
  - 项目/<project>
  - 项目文档/需求
---
```

Use these conventions:

- Full vault-relative WikiLinks without `.md`: `[[path/to/note|label]]`.
- Heading/block links for precise ownership: `[[note#Heading|label]]`, `[[note#^block-id|label]]`.
- `> [!abstract] 文档职责` at the top of each note.
- `warning` for conflicts, `danger` for release gates, `important` for invariants, `success` for exit criteria.
- Mermaid only when relationships or state transitions are materially clearer than prose.
- Obsidian tasks (`- [ ]`) for unresolved work; do not encode pending work only in tables.
- Built-in `query` blocks only when core Search is enabled and the query is stable.
- Escape WikiLink alias pipes as `\|` inside Markdown tables, or keep aliased links out of tables.

Requirements describe “what”; technical notes describe “how”; acceptance notes describe “how to prove it.” Keep exact OpenAPI/Schema fields in their contract source rather than duplicating them into architecture notes.

### 5. Validate and repair

Run:

```bash
python3 <skill-root>/scripts/obsidian_docs_guard.py validate \
  --vault-root <vault-root> \
  --target <target-dir> \
  --manifest <source-manifest.json> \
  --forbid-local-markdown-links
```

Fix every error. Then verify:

- all source hashes match the pre-edit manifest;
- Git shows no new source-directory modifications;
- every note has valid Properties and a responsibility Callout;
- every WikiLink resolves, code fences balance, and local links use WikiLink syntax;
- requirements have stable IDs and acceptance coverage;
- recommendations carry an “as of” date and open items remain visibly unresolved.

### 6. Hand off

Lead with the target MOC. Report note count, responsibility split, important unresolved conflicts, validation result, and explicit confirmation that sources were unchanged. Mention pre-existing source changes separately so they are not mistaken for this run's edits.

## Non-negotiable Rules

- Never modify, rename, reformat, or “clean up” source documents.
- Never follow a symlink and write through it.
- Never invent authority for conflicting facts.
- Never install or require an Obsidian plugin merely to render the generated notes.
- Never replace ADR history or research evidence with the synthesized notes.
- Never stage or commit unless the user separately requests it.

## Bundled Guard

`scripts/obsidian_docs_guard.py` is read-only. Use `snapshot` before edits and `validate` after edits. It checks source integrity, Properties, WikiLinks, responsibility Callouts, code fences, table-link escaping, and optional prohibition of local Markdown links.
