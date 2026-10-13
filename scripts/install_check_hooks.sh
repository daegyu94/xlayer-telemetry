#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
[[ $# -eq 0 ]] || { echo 'Usage: bash scripts/install_check_hooks.sh' >&2; exit 2; }
hooks_dir=$(git config --path --get core.hooksPath || git rev-parse --git-path hooks)
[[ $hooks_dir == /* ]] || hooks_dir="$repo_root/$hooks_dir"
mkdir -p "$hooks_dir"

for name in pre-commit pre-push; do
  profile=fast
  [[ $name == pre-push ]] && profile=push
  target="$hooks_dir/$name"
  content=$(cat <<EOF
#!/usr/bin/env bash
# xlayer-check-hook v2
set -euo pipefail
root=\$(git rev-parse --show-toplevel)
exec bash "\$root/scripts/check.sh" $profile
EOF
)
  if [[ -e $target || -L $target ]]; then
    if [[ $(cat "$target") == "$content" ]]; then
      echo "$name already installed: $target"
      continue
    fi
    legacy_profile=fast
    [[ $name == pre-push ]] && legacy_profile=default
    legacy=$(cat <<EOF
#!/usr/bin/env bash
# xlayer-check-hook v1
set -euo pipefail
root=\$(git rev-parse --show-toplevel)
exec bash "\$root/scripts/check.sh" $legacy_profile
EOF
)
    if [[ $(cat "$target") != "$legacy" ]]; then
      echo "Existing $name preserved: $target; chain scripts/check.sh $profile manually." >&2
      continue
    fi
  fi
  printf '%s\n' "$content" > "$target"
  chmod +x "$target"
  echo "Installed $target"
done
