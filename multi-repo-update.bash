#!/usr/bin/env bash
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

# Repository updater - safer, more portable, and more informative
# Usage:
#   ./multi-repo-update [OPTIONS] [DIR]
#   If DIR is provided, only that directory is processed.
# Options:
#   -h, --help             Show this help and exit
#   -j, --jobs N           Parallel fetch jobs for 'git fetch' (default from $JOBS or 4)
#   -c, --color MODE       Color output: auto|always|never (default from $USE_COLOR or auto)
#       --range REF        Ref to mark before pull and diff against (default from $LOG_RANGE_REF)
#   -r, --recursive        Recurse into nested subdirectories
#       --max-depth N      Limit recursion depth (1 = only top level). Ignored without -r
#       --fetch-only       Only fetch remotes and tags, do not pull
# Env:
#   JOBS, USE_COLOR, LOG_RANGE_REF as above

set -Eeuo pipefail
shopt -s nullglob

JOBS=${JOBS:-4}
USE_COLOR=${USE_COLOR:-auto}
LOG_RANGE_REF=${LOG_RANGE_REF:-refs/heads/lastpull}
TARGET_DIR=""
RECURSIVE=false
MAX_DEPTH=""  # unset means unlimited when recursive
FETCH_ONLY=false
# Keep color vars defined for early error paths before setup_colors runs.
red=; green=; yellow=; cyan=; bold=; reset=

usage() {
  cat <<USAGE
Repository updater - update many git repos safely

Usage:
  $(basename "$0") [OPTIONS] [DIR]

Options:
  -h, --help             Show this help and exit
  -j, --jobs N           Parallel fetch jobs for 'git fetch' (default: ${JOBS})
  -c, --color MODE       Color output: auto|always|never (default: ${USE_COLOR})
      --range REF        Ref used to record pre-pull state (default: ${LOG_RANGE_REF})
  -r, --recursive        Recurse into nested subdirectories
      --max-depth N      Limit recursion depth (1 = only top level). Ignored without -r
      --fetch-only       Only fetch remotes and tags, do not pull

Env:
  JOBS, USE_COLOR, LOG_RANGE_REF - same as corresponding options

Examples:
  JOBS=8 $(basename "$0")
  $(basename "$0") -j 8 sdk
  $(basename "$0") -r --max-depth 2
  $(basename "$0") --fetch-only
USAGE
}

is_positive_int() {
  [[ $1 =~ ^[1-9][0-9]*$ ]]
}

# Parse short/long options in one pass. Supports mixed option/positional order
# and --opt=value forms for long options.
parse_args() {
  local positional=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        usage
        exit 0
        ;;
      -j|--jobs)
        [[ $# -ge 2 ]] || { err "$1 requires a value"; exit 2; }
        JOBS="$2"
        shift 2
        ;;
      --jobs=*)
        JOBS=${1#*=}
        shift
        ;;
      -j*)
        JOBS=${1#-j}
        shift
        ;;
      -c|--color)
        [[ $# -ge 2 ]] || { err "$1 requires a value"; exit 2; }
        USE_COLOR="$2"
        shift 2
        ;;
      --color=*)
        USE_COLOR=${1#*=}
        shift
        ;;
      -c*)
        USE_COLOR=${1#-c}
        shift
        ;;
      --range=*)
        LOG_RANGE_REF=${1#*=}
        shift
        ;;
      --range)
        [[ $# -ge 2 ]] || { err "$1 requires a value"; exit 2; }
        LOG_RANGE_REF="$2"
        shift 2
        ;;
      -r|--recursive)
        RECURSIVE=true
        shift
        ;;
      --max-depth)
        [[ $# -ge 2 ]] || { err "--max-depth requires a value"; exit 2; }
        MAX_DEPTH="$2"
        shift 2
        ;;
      --max-depth=*)
        MAX_DEPTH=${1#*=}
        shift
        ;;
      --fetch-only)
        FETCH_ONLY=true
        shift
        ;;
      --)
        shift
        positional+=("$@")
        break
        ;;
      -*)
        err "Unknown option: $1"; usage; exit 2
        ;;
      *)
        positional+=("$1")
        shift
        ;;
    esac
  done
  set -- "${positional[@]}"

  if ! is_positive_int "$JOBS"; then
    err "Invalid --jobs value (must be a positive integer): $JOBS"
    exit 2
  fi

  if [[ -n "$MAX_DEPTH" ]] && ! is_positive_int "$MAX_DEPTH"; then
    err "Invalid --max-depth value (must be a positive integer): $MAX_DEPTH"
    exit 2
  fi

  # Remaining non-option argument - target directory (optional)
  if [[ $# -gt 1 ]]; then
    err "Too many arguments: $*"; usage; exit 2
  fi
  if [[ $# -eq 1 ]]; then
    TARGET_DIR="$1"
  fi
}

setup_colors() {
  red=; green=; yellow=; cyan=; bold=; reset=
  if [[ -n ${NO_COLOR:-} || ${USE_COLOR} == never ]]; then
    return
  fi

  if [[ ${USE_COLOR} == auto && ! -t 1 ]]; then
    return
  fi

  if command -v tput >/dev/null 2>&1 && tput colors >/dev/null 2>&1; then
    red=$(tput setaf 1)
    green=$(tput setaf 2)
    yellow=$(tput setaf 3)
    cyan=$(tput setaf 6)
    bold=$(tput bold)
    reset=$(tput sgr0)
  elif [[ ${USE_COLOR} == always ]]; then
    # Fallback when forcing colors without a usable tput/terminal database.
    red=$'\033[31m'
    green=$'\033[32m'
    yellow=$'\033[33m'
    cyan=$'\033[36m'
    bold=$'\033[1m'
    reset=$'\033[0m'
  fi
}

warn() { printf '%b\n' "${yellow}${bold}WARNING:${reset} $*"; }
info() { printf '%b\n' "${cyan}${bold}==>${reset} $*"; }
err()  { printf '%b\n' "${red}${bold}ERROR:${reset} $*" >&2; }

require() {
  command -v "$1" >/dev/null 2>&1 || { err "missing dependency: $1"; exit 127; }
}

is_git_repo() {
  git -C "$1" rev-parse --git-dir >/dev/null 2>&1
}

is_bare_repo() {
  [[ $(git -C "$1" rev-parse --is-bare-repository 2>/dev/null || echo false) == true ]]
}

is_git_candidate() {
  local dir=$1
  [[ -d "$dir/.git" || -f "$dir/.git" ]] && return 0
  [[ -f "$dir/HEAD" && -d "$dir/objects" ]] || return 1
  [[ -d "$dir/refs" || -f "$dir/packed-refs" ]]
}

# Update a single repository directory (bare or non-bare)
_update_one() {
  local dir=$1
  [[ -d "$dir" ]] || { warn "$dir is not a directory - skipping"; return 0; }
  if ! is_git_repo "$dir"; then
    warn "$dir is not a git repository - skipping"
    return 0
  fi

  printf '%b\n' "${green}${bold}${dir}${reset}"

  # Mark current state so we can show a compact log after update
  git -C "$dir" update-ref "$LOG_RANGE_REF" HEAD || true

  # Incremental fetch from all remotes, including tags.
  git -C "$dir" fetch --all --tags --jobs="$JOBS" --prune --force

  if [[ "$FETCH_ONLY" == true ]]; then
    info "fetch-only mode - skipping pull for $dir"
  else
    if is_bare_repo "$dir"; then
      info "$dir is a bare repository - skipping pull"
    else
      # Pull current branch (fast-forward only by default to avoid accidental merges)
      if ! git -C "$dir" pull --prune --ff-only; then
        warn "$dir pull was not fast-forward - leaving repository unchanged"
      fi
    fi
  fi

  # Show what changed since the saved ref, if any
  if git -C "$dir" rev-parse -q --verify "$LOG_RANGE_REF" >/dev/null; then
    git -C "$dir" log --oneline --graph --decorate "$LOG_RANGE_REF"..HEAD || true
  fi

  printf '\n'
}

# Public function: if DIR passed, update that; otherwise traverse directories
update_repo() {
  if [[ $# -eq 1 ]]; then
    _update_one "$1"
    return
  fi

  if [[ "$RECURSIVE" == true ]]; then
    local find_args=( . -mindepth 1 -type d )
    if [[ -n "$MAX_DEPTH" ]]; then
      find_args+=( -maxdepth "$MAX_DEPTH" )
    fi
    # Use NUL delimiters so names with spaces/newlines are handled safely.
    while IFS= read -r -d '' d; do
      # Strip leading ./ for nicer output.
      d=${d#./}
      is_git_candidate "$d" || continue
      _update_one "$d"
    done < <(find "${find_args[@]}" -print0)
    return
  fi

  local d
  for d in *; do
    [[ -d "$d" ]] || continue
    _update_one "$d"
  done
}

main() {
  parse_args "$@"
  require git
  setup_colors

  if [[ -n "$TARGET_DIR" ]]; then
    if [[ ! -d "$TARGET_DIR" ]]; then
      err "Not a directory: $TARGET_DIR"
      exit 1
    fi
    if [[ "$RECURSIVE" == true ]]; then
      (
        cd -- "$TARGET_DIR"
        update_repo
      )
    else
      update_repo "$TARGET_DIR"
    fi
  else
    update_repo
  fi
}

main "$@"
