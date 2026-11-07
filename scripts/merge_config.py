#!/usr/bin/env python3
"""Install the project's config.yaml into HERMES_HOME without losing runtime state.

Usage: merge_config.py <project config.yaml> <HERMES_HOME/config.yaml>

The project's config.yaml is the source of truth, but Hermes also writes into
its own config.yaml at runtime: /sethome stores platforms.<name>.home_channel
there. Copying the project file over it on every start would forget the home
channel at each restart, so those values are carried over.

Needs PyYAML (python3-yaml on Debian and Ubuntu). Without it the project file
is copied as is, which is what start.sh did before.
"""

import os
import shutil
import sys
import tempfile

# Values Hermes saves at runtime that the project file does not set.
RUNTIME_KEYS = ("home_channel",)


def main():
    source, target = sys.argv[1], sys.argv[2]
    try:
        import yaml
    except ImportError:
        shutil.copyfile(source, target)
        print("merge_config: PyYAML is not installed; copied config.yaml as is", file=sys.stderr)
        return

    with open(source, encoding="utf-8") as handle:
        merged = yaml.safe_load(handle) or {}
    previous = {}
    if os.path.exists(target):
        with open(target, encoding="utf-8") as handle:
            previous = yaml.safe_load(handle) or {}

    kept = []
    for platform, settings in (previous.get("platforms") or {}).items():
        if not isinstance(settings, dict):
            continue
        for key in RUNTIME_KEYS:
            if key not in settings:
                continue
            slot = merged.setdefault("platforms", {}).setdefault(platform, {})
            if key not in slot:
                slot[key] = settings[key]
                kept.append(f"platforms.{platform}.{key}")

    directory = os.path.dirname(os.path.abspath(target))
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as handle:
        yaml.safe_dump(merged, handle, sort_keys=False, allow_unicode=True)
        temporary = handle.name
    os.replace(temporary, target)
    if kept:
        print("merge_config: kept " + ", ".join(kept), file=sys.stderr)


if __name__ == "__main__":
    main()
