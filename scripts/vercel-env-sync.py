#!/usr/bin/env python3
"""Sync local .env-style variables to the linked Vercel project."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from dotenv import dotenv_values as third_party_dotenv_values
except ImportError:
    third_party_dotenv_values = None


DEFAULT_ENVIRONMENTS = ("development", "preview", "production")
LOCAL_ONLY_KEYS = {"PORT", "FRONTEND_PORT", "BACKEND_PORT"}
SENSITIVE_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "URI")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_cli() -> list[str]:
    if shutil.which("vercel"):
        return ["vercel"]
    if shutil.which("npx"):
        return ["npx", "vercel@latest"]
    raise RuntimeError("Neither 'vercel' nor 'npx' is available on PATH.")


def ensure_linked_project(root: Path) -> None:
    if not (root / ".vercel" / "project.json").exists():
        raise RuntimeError(
            "This repo is not linked to a Vercel project locally. Run 'bash scripts/vercel-project-status.sh' and then 'bash scripts/vercel-link.sh' or 'npx vercel@latest link'."
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync .env.local style keys to the linked Vercel project.")
    parser.add_argument("--file", default=".env.local", help="Env file to read. Defaults to .env.local")
    parser.add_argument(
        "--environment",
        action="append",
        choices=DEFAULT_ENVIRONMENTS,
        help="Target environment. Repeat to target multiple. Defaults to development, preview, and production.",
    )
    parser.add_argument(
        "--include-local-only",
        action="store_true",
        help="Also sync local-only keys like PORT, FRONTEND_PORT, and BACKEND_PORT.",
    )
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="Also sync empty-string values. By default empty values are skipped for safety.",
    )
    parser.add_argument(
        "--preview-git-branch",
        default=os.environ.get("VERCEL_PREVIEW_GIT_BRANCH"),
        help=(
            "Git branch target for Preview environment syncs. "
            "Can also be provided through VERCEL_PREVIEW_GIT_BRANCH."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print what would be synced without calling Vercel.")
    return parser.parse_args()


def is_sensitive(name: str) -> bool:
    upper_name = name.upper()
    return any(marker in upper_name for marker in SENSITIVE_MARKERS)


def load_values(env_file: Path, include_local_only: bool, include_empty: bool) -> tuple[dict[str, str], list[str]]:
    parsed = read_env_file(env_file)
    values: dict[str, str] = {}
    skipped: list[str] = []

    for key, value in parsed.items():
        if key is None:
            continue
        if not include_local_only and key in LOCAL_ONLY_KEYS:
            skipped.append(f"{key} (local-only)")
            continue
        if value is None:
            skipped.append(f"{key} (unset)")
            continue
        if value == "" and not include_empty:
            skipped.append(f"{key} (empty)")
            continue
        values[key] = value

    return values, skipped


def read_env_file(env_file: Path) -> dict[str, str | None]:
    if third_party_dotenv_values is not None:
        return dict(third_party_dotenv_values(env_file))

    parsed: dict[str, str | None] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        parsed[key] = value
    return parsed


def sync_value(
    root: Path,
    cli: list[str],
    name: str,
    value: str,
    environment: str,
    dry_run: bool,
    preview_git_branch: str | None,
) -> None:
    command = [*cli, "env", "add", name, environment]
    if environment == "preview":
        # Vercel CLI 54.x may still prompt for branch selection unless the
        # optional preview-branch slot is explicitly present. Empty means "all
        # preview branches"; a non-empty value scopes the variable to one branch.
        command.append(preview_git_branch or "")
    command.extend(["--force", "--yes", "--value", value])
    if environment in {"preview", "production"} and is_sensitive(name):
        command.append("--sensitive")

    if dry_run:
        preview_command = command.copy()
        value_index = preview_command.index("--value") + 1
        preview_command[value_index] = "<redacted>"
        print(f"[dry-run] {' '.join(preview_command)}")
        return

    subprocess.run(command, cwd=root, check=True)


def main() -> int:
    args = parse_args()
    root = repo_root()
    env_file = root / args.file

    if not env_file.exists():
        print(f"ERROR: env file not found: {env_file}", file=sys.stderr)
        return 1

    try:
        if args.dry_run:
            cli = ["vercel"]
        else:
            cli = resolve_cli()
            ensure_linked_project(root)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    environments = args.environment or list(DEFAULT_ENVIRONMENTS)
    values, skipped = load_values(env_file, args.include_local_only, args.include_empty)

    if not values:
        print("No values to sync. Everything was empty or skipped.", file=sys.stderr)
        return 1

    print(f"Syncing {len(values)} keys from {env_file.name} to environments: {', '.join(environments)}")
    if skipped:
        print("Skipped:")
        for item in skipped:
            print(f"  - {item}")

    for environment in environments:
        print(f"\nEnvironment: {environment}")
        for name, value in values.items():
            sync_value(root, cli, name, value, environment, args.dry_run, args.preview_git_branch)
            print(f"  synced {name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
