# multi-repo-update

Keep many Git repositories updated from one command.

This directory contains two equivalent scripts:

- `multi-repo-update` (Bash)
- `multi-repo-update.py` (Python)

## Use Case

Designed for workflows where you:

- monitor several repositories at once
- keep them updated with upstream changes
- see when upstream adds new branches or tags, or deletes them
- review commits that arrived since the previous run

## What It Does Per Repository

For each detected repository, the scripts run this flow:

1. Save current `HEAD` to a tracking ref (default `refs/heads/lastpull`).
2. Run:

```bash
git fetch --all --tags --jobs=<N> --prune --force
```

This is where Git reports newly discovered upstream refs (for example new
branches and tags), because `git fetch` prints them.

3. If not `--fetch-only`:
   - non-bare repo: `git pull --prune --ff-only`
   - bare repo: skip pull
4. Show:

```bash
git log --oneline --graph --decorate <range-ref>..HEAD
```

This shows commits that became part of `HEAD` during this run, which gives you
"what changed since last run" visibility.

## Requirements

- `git` in `PATH` (required by both scripts)
- Bash script:
  - `bash`
  - optional `tput` for color support
- Python script:
  - Python `3.12+` (uses `pathlib.Path.walk`; enforced at startup)
  - optional `pydantic` (if installed, config model uses it; otherwise it
    falls back to dataclass + argparse validation)

## Usage

Run from a directory that contains repositories:

```bash
multi-repo-update
```

Or target a specific directory:

```bash
multi-repo-update /path/to/repos
```

Recursive scan:

```bash
multi-repo-update -r --max-depth 2 /path/to/repos
```

Monitoring mode (fetch only, no pull):

```bash
multi-repo-update --fetch-only -r /path/to/repos
```

Python version:

```bash
multi-repo-update.py --fetch-only -r /path/to/repos
```

## Command-Line Options

Both scripts support the same options:

- `-h`, `--help`: show help
- `-j`, `--jobs N`: parallel jobs for `git fetch` (must be positive integer)
- `-c`, `--color MODE`: `auto|always|never`
- `--range REF`: ref used to mark pre-update state (default:
  `refs/heads/lastpull`)
- `-r`, `--recursive`: recurse into nested subdirectories
- `--max-depth N`: depth limit for recursive mode (`1` means direct children
  only)
- `--fetch-only`: fetch remotes and tags but do not pull
- `[DIR]`: optional target directory

## Environment Variables

- `JOBS`: default value for `--jobs` (default `4`)
- `USE_COLOR`: default for `--color` (`auto`, `always`, `never`)
- `LOG_RANGE_REF`: default for `--range` (default `refs/heads/lastpull`)
- `NO_COLOR`: disable colors (respected by both scripts)

CLI options override environment values.

## Notes on Branches, Tags, and Commits

- New upstream branches/tags:
  - shown by `git fetch` output (`[new branch] ...`, `[new tag] ...`)
- Commits since last run:
  - shown by `git log <range-ref>..HEAD` after fetch/pull
- Fast-forward safety:
  - pull is `--ff-only`; non-FF situations are not auto-merged

## Recursive Behavior

- Without `-r`: process top-level repositories.
- With `-r`: walk subdirectories and process Git candidates.
- With `--max-depth N`: limit recursion depth while using `-r`.

## Exit Behavior

- Missing `git`: exits `127`
- Invalid CLI values: exits with argument error
- Non-directory target: exits `1`
- Command failure (for example failing `git fetch`): exits non-zero and stops

## Bash vs Python

The scripts are intentionally aligned in functionality.

- Bash script:
  - minimal runtime dependencies
  - optional `tput`-based color detection
- Python script:
  - typed implementation and structured code
  - optional `pydantic` model validation when available

Choose whichever fits your environment.

## License

MIT License. See `LICENSE`.

## Maintainer

- Jere Viikari
- Project: https://github.com/jereviikari/multi-repo-update
