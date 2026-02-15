#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Jere Viikari
# SPDX-License-Identifier: MIT
# Maintainer: Jere Viikari
# Project: https://github.com/jereviikari/multi-repo-update
#
# MIT License
#
# Copyright (c) 2026 Jere Viikari
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Repository updater for many git repositories."""

import argparse
import os
import shlex
import shutil
# Subprocess is used with fixed argv lists and shell=False.
import subprocess  # nosec B404
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Self

try:
    from pydantic import BaseModel  # pylint: disable=import-error
    HAS_PYDANTIC = True
except ImportError:
    BaseModel = object
    HAS_PYDANTIC = False


DEFAULT_JOBS: Final[str] = os.environ.get("JOBS", "4")
DEFAULT_COLOR: Final[str] = os.environ.get("USE_COLOR", "auto")
DEFAULT_RANGE_REF: Final[str] = os.environ.get(
    "LOG_RANGE_REF", "refs/heads/lastpull"
)
MIN_PYTHON: Final[tuple[int, int]] = (3, 12)


class ColorMode(StrEnum):
    """Supported output color behavior modes."""

    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


if HAS_PYDANTIC:

    class AppConfig(BaseModel):  # pylint: disable=too-few-public-methods
        """Validated runtime configuration for the updater."""

        jobs: int
        color_mode: ColorMode
        log_range_ref: str
        target_dir: str | None
        recursive: bool
        max_depth: int | None
        fetch_only: bool

else:

    @dataclass(slots=True, frozen=True)
    class AppConfig:
        """Runtime configuration for the updater when pydantic is absent."""

        jobs: int
        color_mode: ColorMode
        log_range_ref: str
        target_dir: str | None
        recursive: bool
        max_depth: int | None
        fetch_only: bool


@dataclass(slots=True, frozen=True)
class Colors:
    """ANSI escape sequences used by log output."""

    red: str = ""
    green: str = ""
    yellow: str = ""
    cyan: str = ""
    bold: str = ""
    reset: str = ""


class Logger:
    """Small console logger with colored prefixes."""

    def __init__(self: Self, colors: Colors) -> None:
        self.colors = colors

    def warn(self: Self, message: str) -> None:
        """Print a warning message to stdout."""

        print(
            f"{self.colors.yellow}{self.colors.bold}"
            f"WARNING:{self.colors.reset} {message}"
        )

    def info(self: Self, message: str) -> None:
        """Print an informational message to stdout."""

        print(
            f"{self.colors.cyan}{self.colors.bold}"
            f"==>{self.colors.reset} {message}"
        )

    def error(self: Self, message: str) -> None:
        """Print an error message to stderr."""

        print(
            f"{self.colors.red}{self.colors.bold}"
            f"ERROR:{self.colors.reset} {message}",
            file=sys.stderr,
        )


class RepoUpdater:  # pylint: disable=too-few-public-methods
    """Update one or many git repositories according to AppConfig."""

    def __init__(self: Self, config: AppConfig, logger: Logger) -> None:
        self.config = config
        self.logger = logger

    def run(self: Self) -> int:
        """Execute update flow and return a process-style exit code."""

        if self.config.target_dir is None:
            self._update_from_cwd()
            return 0

        target = Path(self.config.target_dir)
        if not target.is_dir():
            self.logger.error(f"Not a directory: {self.config.target_dir}")
            return 1

        if self.config.recursive:
            self._update_recursive(target)
            return 0

        self._update_one(target, self.config.target_dir)
        return 0

    def _update_from_cwd(self: Self) -> None:
        """Update repositories discovered from current working directory."""

        if self.config.recursive:
            self._update_recursive(Path("."))
            return

        for child in sorted(Path(".").iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            if not self._is_git_candidate(child):
                continue
            self._update_one(child, child.name)

    def _update_recursive(self: Self, base_dir: Path) -> None:
        """Traverse subdirectories and update git candidates."""

        for repo_dir, display in self._iter_subdirs(
            base_dir, self.config.max_depth
        ):
            if not self._is_git_candidate(repo_dir):
                continue
            self._update_one(repo_dir, display)

    def _iter_subdirs(
        self: Self, base_dir: Path, max_depth: int | None
    ) -> Iterator[tuple[Path, str]]:
        """Yield subdirectories and display names relative to base_dir."""

        for root_path, dirs, _ in base_dir.walk():
            dirs.sort()
            rel = root_path.relative_to(base_dir)
            depth = len(rel.parts)

            if max_depth is not None and depth >= max_depth:
                dirs[:] = []

            if depth == 0:
                continue

            yield root_path, rel.as_posix()

    @staticmethod
    def _is_git_candidate(path: Path) -> bool:
        """Cheap filesystem check to avoid probing obvious non-repositories."""

        git_meta = path / ".git"
        if git_meta.is_dir() or git_meta.is_file():
            return True

        has_bare_layout = (
            (path / "HEAD").is_file()
            and (path / "objects").is_dir()
        )
        if not has_bare_layout:
            return False

        return (path / "refs").is_dir() or (path / "packed-refs").is_file()

    def _git(
        self: Self,
        path: Path,
        args: Sequence[str],
        *,
        check: bool = False,
    ) -> subprocess.CompletedProcess:
        """Run a git command in path."""

        cmd = ["git", "-C", str(path), *args]
        # Command is an argv list, not a shell string.
        return subprocess.run(cmd, check=check)  # nosec B603

    def _git_ok(self: Self, path: Path, args: Sequence[str]) -> bool:
        """Return True when a git command succeeds."""

        cmd = ["git", "-C", str(path), *args]
        # Command is an argv list, not a shell string.
        result = subprocess.run(  # nosec B603
            cmd,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0

    def _git_output(self: Self, path: Path, args: Sequence[str]) -> str:
        """Return stripped stdout from a successful command.

        Return an empty string when the command fails.
        """

        cmd = ["git", "-C", str(path), *args]
        # Command is an argv list, not a shell string.
        result = subprocess.run(  # nosec B603
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def _is_git_repo(self: Self, path: Path) -> bool:
        """Check whether path points to a valid git repository."""

        return self._git_ok(path, ["rev-parse", "--git-dir"])

    def _is_bare_repo(self: Self, path: Path) -> bool:
        """Check whether repository at path is bare."""

        return (
            self._git_output(path, ["rev-parse", "--is-bare-repository"])
            == "true"
        )

    def _has_ref(self: Self, path: Path, ref_name: str) -> bool:
        """Check if ref_name exists in repository at path."""

        return self._git_ok(path, ["rev-parse", "-q", "--verify", ref_name])

    def _update_one(self: Self, path: Path, display_name: str) -> None:
        """Update a single repository and print progress/log output."""

        if not path.is_dir():
            self.logger.warn(f"{display_name} is not a directory - skipping")
            return

        if not self._is_git_repo(path):
            self.logger.warn(
                f"{display_name} is not a git repository - skipping"
            )
            return

        print(
            f"{self.logger.colors.green}"
            f"{self.logger.colors.bold}"
            f"{display_name}{self.logger.colors.reset}"
        )

        self._git(path, ["update-ref", self.config.log_range_ref, "HEAD"])
        self._git(
            path,
            [
                "fetch",
                "--all",
                "--tags",
                f"--jobs={self.config.jobs}",
                "--prune",
                "--force",
            ],
            check=True,
        )

        if self.config.fetch_only:
            self.logger.info(
                f"fetch-only mode - skipping pull for {display_name}"
            )
        elif self._is_bare_repo(path):
            self.logger.info(
                f"{display_name} is a bare repository - skipping pull"
            )
        else:
            pull_result = self._git(path, ["pull", "--prune", "--ff-only"])
            if pull_result.returncode != 0:
                self.logger.warn(
                    f"{display_name} pull was not fast-forward - "
                    "leaving repository unchanged"
                )

        if self._has_ref(path, self.config.log_range_ref):
            self._git(
                path,
                [
                    "log",
                    "--oneline",
                    "--graph",
                    "--decorate",
                    f"{self.config.log_range_ref}..HEAD",
                ],
            )

        print()


def build_colors(mode: ColorMode) -> Colors:
    """Return ANSI color palette according to mode and terminal state."""

    if os.getenv("NO_COLOR") is not None or mode == ColorMode.NEVER:
        return Colors()

    if mode == ColorMode.AUTO and not sys.stdout.isatty():
        return Colors()

    return Colors(
        red="\033[31m",
        green="\033[32m",
        yellow="\033[33m",
        cyan="\033[36m",
        bold="\033[1m",
        reset="\033[0m",
    )


def _parse_positive_int(value: str, option_name: str) -> int:
    """Parse a strictly positive integer for argparse options."""

    message = (
        f"Invalid {option_name} value "
        f"(must be a positive integer): {value}"
    )
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(message) from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError(message)
    return parsed


def parse_args(argv: Sequence[str]) -> AppConfig:
    """Parse CLI arguments and return validated AppConfig."""

    usage_hint = None
    if not HAS_PYDANTIC:
        usage_hint = (
            "Note: optional dependency 'pydantic' is not installed; "
            "using argparse-only validation."
        )
    parser = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        description="Repository updater - update many git repos safely",
        epilog=usage_hint,
    )
    try:
        default_jobs = _parse_positive_int(DEFAULT_JOBS, "--jobs")
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))

    try:
        default_color = ColorMode(DEFAULT_COLOR)
    except ValueError:
        parser.error(
            "Invalid USE_COLOR env value "
            f"(must be one of auto|always|never): {DEFAULT_COLOR}"
        )

    parser.add_argument(
        "-j",
        "--jobs",
        default=default_jobs,
        type=lambda value: _parse_positive_int(value, "--jobs"),
        metavar="N",
        help="Parallel fetch jobs",
    )
    parser.add_argument(
        "-c",
        "--color",
        default=default_color,
        type=ColorMode,
        choices=tuple(ColorMode),
        metavar="MODE",
        help="Color output: auto|always|never",
    )
    parser.add_argument(
        "--range",
        dest="log_range_ref",
        default=DEFAULT_RANGE_REF,
        metavar="REF",
        help="Ref used to record pre-pull state",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Recurse into nested subdirectories",
    )
    parser.add_argument(
        "--max-depth",
        default=None,
        type=lambda value: _parse_positive_int(value, "--max-depth"),
        metavar="N",
        help="Limit recursion depth (ignored without -r/--recursive)",
    )
    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help="Only fetch remotes and tags, do not pull",
    )
    parser.add_argument("dir", nargs="?", help="Optional target directory")

    ns = parser.parse_intermixed_args(argv)
    config_data = {
        "jobs": ns.jobs,
        "color_mode": ns.color,
        "log_range_ref": ns.log_range_ref,
        "target_dir": ns.dir,
        "recursive": ns.recursive,
        "max_depth": ns.max_depth,
        "fetch_only": ns.fetch_only,
    }
    if HAS_PYDANTIC:
        return AppConfig.model_validate(config_data)
    return AppConfig(**config_data)


def require_git(logger: Logger) -> bool:
    """Ensure git is available in PATH."""

    if shutil.which("git") is not None:
        return True
    logger.error("missing dependency: git")
    return False


def require_python_version() -> bool:
    """Ensure the running interpreter matches minimum supported version."""

    if sys.version_info >= MIN_PYTHON:
        return True
    required = ".".join(str(part) for part in MIN_PYTHON)
    found = (
        f"{sys.version_info.major}."
        f"{sys.version_info.minor}."
        f"{sys.version_info.micro}"
    )
    print(
        f"ERROR: Python {required}+ is required (found {found})",
        file=sys.stderr,
    )
    return False


def main(argv: Sequence[str] | None = None) -> int:
    """Program entrypoint returning a shell-friendly exit code."""

    if not require_python_version():
        return 2

    args = parse_args(sys.argv[1:] if argv is None else argv)
    logger = Logger(build_colors(args.color_mode))

    if not require_git(logger):
        return 127

    updater = RepoUpdater(args, logger)
    try:
        return updater.run()
    except subprocess.CalledProcessError as exc:
        cmd = " ".join(shlex.quote(str(part)) for part in exc.cmd)
        logger.error(f"command failed ({exc.returncode}): {cmd}")
        return exc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
