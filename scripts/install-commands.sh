#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
command_dir=${ICARUS_BIN_DIR:-${HOME:?HOME is required}/.local/bin}

mkdir -p "$command_dir"

install_command() {
  command_name=$1
  source_path="$repo_root/bin/$command_name"
  target_path="$command_dir/$command_name"

  if [ -L "$target_path" ]; then
    if [ "$(readlink "$target_path")" = "$source_path" ]; then
      return
    fi
    echo "Refusing to replace unrelated symlink: $target_path" >&2
    exit 1
  fi

  if [ -e "$target_path" ]; then
    expected_entry="apps.tui.src.main"
    if [ "$command_name" = "icarus-gateway" ]; then
      expected_entry="apps.gateway.src.main"
    fi
    if grep -q "$expected_entry" "$target_path" 2>/dev/null; then
      rm "$target_path"
    else
      echo "Refusing to replace unrelated command: $target_path" >&2
      exit 1
    fi
  fi

  ln -s "$source_path" "$target_path"
}

install_command icarus
install_command icarus-gateway

echo "Installed Icarus commands in $command_dir"
case :$PATH: in
  *:"$command_dir":*) ;;
  *) echo "Add $command_dir to PATH to use icarus and icarus-gateway" ;;
esac
