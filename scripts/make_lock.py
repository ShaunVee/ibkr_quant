"""One-shot helper: generate requirements.lock from a pip resolver report.

Usage: python scripts/make_lock.py /tmp/quantbot_resolve.json
"""
import json
import sys

report = json.load(open(sys.argv[1]))
lines = []
for item in report["install"]:
    meta = item["metadata"]
    if meta["name"].lower() == "quantbot":
        continue
    lines.append(f"{meta['name']}=={meta['version']}")

header = (
    "# Generated from pyproject.toml's runtime dependencies via a pip resolver\n"
    "# dry-run (scripts/make_lock.py). Regenerate deliberately when bumping deps:\n"
    "#   pip install --dry-run --ignore-installed --report /tmp/resolve.json .\n"
    "#   python scripts/make_lock.py /tmp/quantbot_resolve.json\n"
    "# The Dockerfile installs from this file so builds are reproducible;\n"
    "# bump it on purpose, not on every `docker compose up --build`.\n"
)
with open("requirements.lock", "w") as fh:
    fh.write(header + "\n".join(sorted(lines, key=str.lower)) + "\n")
print(f"{len(lines)} packages pinned to requirements.lock")
