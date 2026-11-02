#!/usr/bin/env python3
"""Keep the Claude Code manifests in step with the Agent Plugins manifest.

`plugin.json` (repository root, Agent Plugins format) is the one file you edit. This script derives from it:
  * `.claude-plugin/plugin.json`   - the same descriptive fields, without `$schema`, plus `"skills": "./skills/"`
  * `.claude-plugin/marketplace.json` - `metadata.version` and `plugins[0].version` (its other fields are edited by hand)

  python3 tools/sync_manifests.py           # rewrite the derived files
  python3 tools/sync_manifests.py --check   # exit 1 if they are out of date (the tests run this)

Bumping a release = edit "version" in plugin.json, then run this script.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "plugin.json")
CLAUDE_PLUGIN = os.path.join(ROOT, ".claude-plugin", "plugin.json")
MARKETPLACE = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
FIELDS = ("name", "version", "description", "author", "homepage", "repository", "license", "keywords")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def expected():
    """{path: expected text} for the derived files."""
    src = json.loads(read(SOURCE))
    out = {}
    derived = {k: src[k] for k in FIELDS}
    derived["skills"] = "./skills/"
    body = ",\n".join(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in derived.items())
    out[CLAUDE_PLUGIN] = "{\n" + body + "\n}\n"
    text = read(MARKETPLACE)
    market = json.loads(text)
    old_versions = {market["metadata"]["version"], market["plugins"][0]["version"]}
    new_text = text
    for old in old_versions:
        new_text = new_text.replace(f'"version": "{old}"', f'"version": "{src["version"]}"')
    market = json.loads(new_text)
    assert market["metadata"]["version"] == market["plugins"][0]["version"] == src["version"], "marketplace.json has an unexpected shape"
    out[MARKETPLACE] = new_text
    return out


def main(argv):
    check = "--check" in argv
    stale = []
    for path, text in expected().items():
        if read(path) != text:
            stale.append(os.path.relpath(path, ROOT))
            if not check:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
    if stale and check:
        print("out of date (run `python3 tools/sync_manifests.py`): " + ", ".join(stale), file=sys.stderr)
        return 1
    print("updated: " + ", ".join(stale) if stale else "manifests are in sync")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
