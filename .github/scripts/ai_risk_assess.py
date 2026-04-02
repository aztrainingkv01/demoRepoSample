#!/usr/bin/env python3
"""
ai_risk_assess.py — Deterministic "AI starter" risk assessor for PRs.

Produces a JSON document that downstream workflows treat as authoritative input:
{
  "level": "LOW|MEDIUM|HIGH",
  "confidence": 0.0-1.0,
  "reasons": [...],
  "controls": [...],
  "changed_files_count": N,
  "changed_files_sample": [...]
}

You can replace the scoring logic later with an LLM while keeping this schema stable.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import List, Dict


# --- Customize these to your repo (important) ---
RISKY_PATH_PATTERNS = [
    r"^payments/",
    r"^auth/",
    r"^identity/",
    r"^security/",
    r"^infra/",
    r"^terraform/",
    r"^k8s/",
    r"^shared/",
]

DEPENDENCY_FILES = {
    "package-lock.json", "package.json",
    "pom.xml", "build.gradle", "build.gradle.kts",
    "requirements.txt", "poetry.lock", "Pipfile.lock",
    "go.mod", "go.sum",
    ".csproj", "packages.lock.json"
}

FEATURE_FLAG_PATTERNS = [
    r"FEATURE_FLAG:",
    r"FeatureFlags\.isEnabled\(",
    r"feature_flag",
    r"launchdarkly",
    r"unleash",
]


def sh(cmd: str) -> str:
    """Run a shell command and return stdout text."""
    return subprocess.check_output(cmd, shell=True, text=True).strip()


def git_diff_name_only(base: str, head: str) -> List[str]:
    out = sh(f"git diff --name-only {base} {head}")
    return [l for l in out.splitlines() if l.strip()]


def git_diff_patch(base: str, head: str) -> str:
    # Full patch can be large; acceptable for typical PR sizes.
    return sh(f"git diff {base} {head}")


def any_match(patterns: List[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def compute_risk(files: List[str], patch: str) -> Dict:
    reasons = []
    controls = ["CI must pass", "Human approval required"]
    score = 0

    # 1) Scope / size
    if len(files) > 30:
        score += 3
        reasons.append("Large PR: >30 files changed (higher review/merge risk)")
    elif len(files) > 10:
        score += 2
        reasons.append("Medium PR size: >10 files changed")

    # 2) Sensitive areas
    risky_hits = []
    for f in files:
        for pat in RISKY_PATH_PATTERNS:
            if re.match(pat, f):
                risky_hits.append(f)
                break

    if risky_hits:
        score += 4
        reasons.append(f"Touches sensitive area(s) (example: {risky_hits[0]})")
        controls += ["Senior reviewer required", "Rollback plan required", "Feature flag required (default OFF)"]

    # 3) Dependency changes
    if any(Path(f).name in DEPENDENCY_FILES for f in files):
        score += 2
        reasons.append("Dependency file changed (supply chain/compatibility risk)")
        controls += ["Security scan required", "No auto-merge"]

    # 4) Infra / config changes
    if any(f.startswith(("infra/", "terraform/", "k8s/")) for f in files):
        score += 3
        reasons.append("Infrastructure/config change detected")
        controls += ["Platform/Infra reviewer required", "No auto-merge"]

    # 5) Feature flag presence (signal)
    if any_match(FEATURE_FLAG_PATTERNS, patch):
        reasons.append("Feature flag marker detected in diff")
    else:
        # We do not penalize low-risk PRs, but for high-risk later enforcement will catch.
        pass

    # 6) Determine level
    if score >= 7:
        level = "HIGH"
        confidence = 0.85
    elif score >= 4:
        level = "MEDIUM"
        confidence = 0.75
        controls += ["Feature flag recommended"]
    else:
        level = "LOW"
        confidence = 0.70
        controls += ["Auto-merge allowed if checks pass"]

    # Deduplicate controls/reasons
    reasons = sorted(set(reasons)) if reasons else ["No elevated risk indicators detected"]
    controls = sorted(set(controls))

    return {
        "level": level,
        "confidence": confidence,
        "reasons": reasons,
        "controls": controls,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="Base commit SHA (e.g., PR base)")
    ap.add_argument("--head", required=True, help="Head commit SHA (e.g., PR head)")
    ap.add_argument("--out", required=True, help="Output JSON path (e.g., ai-risk.json)")
    args = ap.parse_args()

    files = git_diff_name_only(args.base, args.head)
    patch = git_diff_patch(args.base, args.head)

    risk = compute_risk(files, patch)
    result = {
        **risk,
        "changed_files_count": len(files),
        "changed_files_sample": files[:20],
    }

    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

