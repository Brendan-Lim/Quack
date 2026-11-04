#!/usr/bin/env python3
"""Validate the staged public file set and, optionally, all reachable history."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "scripts/public-files.txt"
PUBLIC_DOCS = {
    "README.md",
    "docs/README.md",
    "docs/ARCHITECTURE.md",
    "docs/SETUP.md",
    "docs/TESTING.md",
    "docs/PUBLICATION.md",
    "docs/CREDITS.md",
    "docs/hackathon/JUDGE_GUIDE.md",
    "docs/hackathon/TECHNICAL_OVERVIEW.md",
    "docs/media/README.md",
}
MEDIA_NAMES = {
    "hero.svg",
    "quack.svg",
    "learning-paths.webp",
    "notebook.webp",
    "active-learning.webp",
    "mistake-hint.webp",
    "correction.webp",
    "algebra-roadmap.webp",
    "welcome.webp",
    "mistake-feedback.webp",
    "adaptive-follow-up.webp",
    "level-mastered.webp",
    "my-mistakes.webp",
}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def policy() -> set[str]:
    lines = [
        x.strip()
        for x in POLICY.read_text().splitlines()
        if x.strip() and not x.startswith("#")
    ]
    if len(lines) != len(set(lines)) or any(
        x.startswith("/") or ".." in Path(x).parts or any(c in x for c in "*?[")
        for x in lines
    ):
        raise ValueError(
            "The public file manifest must contain unique, literal relative paths."
        )
    return set(lines)


def forbidden(name: str) -> bool:
    path = Path(name)
    lower = name.lower()
    if path.name.lower() in {"agents.md", "claude.md"}:
        return True
    if any(
        part in {".git", ".aws", ".codex", ".agents", "node_modules", ".venv", "var"}
        for part in path.parts
    ):
        return True
    if path.name.startswith(".env") and name != ".env.example":
        return True
    if name.startswith(("docs/reports/", "evals/results/", "evals/dataset/private/")):
        return True
    if lower.endswith(
        (".mov", ".mp4", ".webm", ".db", ".sqlite", ".sqlite3", ".pem", ".key", ".log")
    ):
        return True
    if re.search(r"\.(db|sqlite3?)-(wal|shm|journal)$", lower):
        return True
    if any(
        word in path.name.lower()
        for word in ("provenance", "receipt", "cleanup_report")
    ):
        return True
    if "ledger" in path.name.lower() and path.suffix.lower() in {
        ".json",
        ".jsonl",
        ".csv",
        ".txt",
    }:
        return True
    if name.startswith("docs/"):
        return name not in PUBLIC_DOCS and not (
            name.startswith("docs/licenses/")
            or (name.startswith("docs/media/") and path.name in MEDIA_NAMES)
        )
    # Versioned prompts are application resources, not development instructions.
    if path.suffix == ".md":
        return name not in PUBLIC_DOCS and not name.startswith(
            "services/api/quack/prompts/"
        )
    return False


def context_text(approved: set[str]) -> str:
    runtime = sorted(
        n
        for n in approved
        if (n.startswith("apps/web/") and "/tests/" not in n and not n.endswith(".md"))
        or n.startswith("services/api/quack/")
        or n
        in {
            "services/api/requirements.txt",
            "services/api/requirements.lock",
            "services/api/pyproject.toml",
        }
    )
    parents = {
        str(parent) for n in runtime for parent in Path(n).parents if str(parent) != "."
    }
    result = [
        "# Runtime build inputs only. Regenerate with check_publication.py --write-dockerignore.",
        "**",
    ]
    result += ["!" + n + "/" for n in sorted(parents, key=lambda x: (x.count("/"), x))]
    result += ["!" + n for n in runtime]
    return "\n".join(result) + "\n"


def blobs(entries: list[tuple[str, str, str]]) -> dict[str, bytes]:
    if not entries:
        return {}
    data = subprocess.check_output(
        ["git", "cat-file", "--batch"],
        cwd=ROOT,
        input="".join(oid + "\n" for _, oid, _ in entries).encode(),
    )
    result = {}
    offset = 0
    for name, _, _ in entries:
        end = data.index(b"\n", offset)
        header = data[offset:end].split()
        if len(header) != 3 or header[1] != b"blob":
            raise ValueError(f"Unexpected Git object for {name}")
        size = int(header[2])
        offset = end + 1
        result[name] = data[offset : offset + size]
        offset += size + 1
    return result


def inspect(
    entries: list[tuple[str, str, str]], approved: set[str], label: str
) -> tuple[list[str], int]:
    errors = []
    names = {n for n, _, _ in entries}
    for name in sorted(names):
        if name not in approved or forbidden(name):
            errors.append(f"{label}: unapproved public path: {name}")
    if label == "index":
        errors += [
            f"index: approved file not staged: {n}" for n in sorted(approved - names)
        ]
    for name, _, mode in entries:
        if mode not in {"100644", "100755"}:
            errors.append(f"{label}: unsupported file mode: {name}")
    content = blobs([entry for entry in entries if entry[2] in {"100644", "100755"}])
    links = 0
    for name, data in content.items():
        if len(data) > 5_000_000:
            errors.append(f"{label}: oversized file: {name}")
        if re.search(
            (
                rb"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}|AKIA[0-9A-Z]{16}|"
                rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
            ),
            data,
        ):
            errors.append(f"{label}: possible credential in {name}; value withheld")
        if name.endswith(".svg"):
            try:
                ET.fromstring(data)
            except ET.ParseError:
                errors.append(f"{label}: invalid SVG: {name}")
        if name not in PUBLIC_DOCS:
            continue
        text = data.decode()
        if re.search(r"/(?:Users|home)/[^/\s]+/", text) or re.search(
            r"CODEX_DEPLOYMENT_HANDOFF|RELEASE_PROVENANCE|PUBLIC_PRIVATE_CLEANUP_REPORT|FINAL_.*REPORT",
            text,
        ):
            errors.append(f"{label}: private working-context reference in {name}")
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        targets = re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text)
        targets += re.findall(r"(?:src|href)=[\"']([^\"']+)[\"']", text)
        for target in targets:
            target = target.strip().strip("<>").split(' "')[0]
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith(("#", "//")):
                continue
            destination = (ROOT / Path(name).parent / unquote(parsed.path)).resolve()
            try:
                relative = destination.relative_to(ROOT).as_posix()
            except ValueError:
                relative = ""
            links += 1
            if relative not in names:
                errors.append(f"{label}: broken public link: {name} -> {target}")
    return errors, links


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--write-dockerignore", action="store_true")
    args = parser.parse_args()
    approved = policy()
    docker = context_text(approved)
    if args.write_dockerignore:
        (ROOT / ".dockerignore").write_text(docker)
        print("Wrote the explicit runtime Docker context policy.")
        return 0
    entries = []
    errors = []
    for record in git("ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        fields, path = record.decode().split("\t", 1)
        mode, oid, stage = fields.split()
        if stage != "0":
            errors.append("Resolve index conflicts before publication.")
            continue
        entries.append((path, oid, mode))
    findings, links = inspect(entries, approved, "index")
    errors += findings
    if (ROOT / ".dockerignore").read_text() != docker:
        errors.append(
            "Runtime Docker context differs from the explicit approved inputs."
        )
    commits = []
    if args.history:
        commits = git("rev-list", "--all").decode().splitlines()
        for commit in commits:
            historical = []
            for record in git("ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
                if not record:
                    continue
                fields, path = record.decode().split("\t", 1)
                mode, _, oid = fields.split()
                historical.append((path, oid, mode))
            findings, _ = inspect(historical, approved, "history " + commit[:12])
            errors += findings
    if args.require_clean and git("status", "--porcelain"):
        errors.append("Commit or remove unintended changes before pushing.")
    for error in errors:
        print(error)
    print(
        f"Public-file check: {len(entries)} staged paths, {links} local links, "
        f"{len(commits)} reachable commits; {len(errors)} errors."
    )
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
