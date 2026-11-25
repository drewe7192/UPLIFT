"""
runner.py — Entry point. Run this from the command line.

Usage:
    python runner.py --project /path/to/your/dotnet/project
    python runner.py --project /path/to/your/dotnet/project --to-version net8.0
"""

import argparse
import os
import re
import subprocess
import sys
from collections import Counter
from dotenv import load_dotenv
from agent import run_phased_migration

load_dotenv()


def detect_source_tfm(project_path: str) -> str:
    """Scan .csproj files to find the most common TargetFramework."""
    tfms = []
    for root, _, files in os.walk(project_path):
        if any(skip in root for skip in ("/obj", "/bin", "/.git")):
            continue
        for fname in files:
            if fname.endswith(".csproj"):
                fpath = os.path.join(root, fname)
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                matches = re.findall(r"<TargetFrameworks?>(.*?)</TargetFrameworks?>", content)
                for m in matches:
                    for part in m.split(";"):
                        part = part.strip()
                        if part:
                            tfms.append(part)
    if not tfms:
        return "netcoreapp3.1"
    # Pick the oldest/most common TFM across projects
    return Counter(tfms).most_common(1)[0][0]


def detect_target_tfm() -> str:
    """Find the highest installed .NET SDK on this machine (e.g., net8.0, net10.0)."""
    try:
        result = subprocess.run(
            ["dotnet", "--list-sdks"], capture_output=True, text=True, check=True
        )
        majors = []
        for line in result.stdout.splitlines():
            match = re.match(r"^(\d+)\.", line.strip())
            if match:
                majors.append(int(match.group(1)))
        if majors:
            highest = max(majors)
            return f"net{highest}.0"
    except Exception:
        pass
    return "net8.0"


def normalize_tfm(version_str: str) -> str:
    """Normalize inputs like '.NET 8', 'NET 3.1', or 'net8.0' into a valid TFM."""
    s = version_str.strip().lower().replace(".net", "").replace("net", "").strip()
    if s in ("3.1", "core3.1", "coreapp3.1"):
        return "netcoreapp3.1"
    if s in ("2.1", "core2.1", "coreapp2.1"):
        return "netcoreapp2.1"
    match = re.match(r"^(\d+)(?:\.(\d+))?$", s)
    if match:
        major = match.group(1)
        minor = match.group(2) or "0"
        return f"net{major}.{minor}"
    return version_str.strip()


def main():
    parser = argparse.ArgumentParser(
        description="AI agent that migrates .NET projects to newer versions."
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Path to the .NET project directory",
    )
    parser.add_argument(
        "--from-version",
        default=None,
        help="Source .NET version (auto-detected from .csproj if omitted)",
    )
    parser.add_argument(
        "--to-version",
        default=None,
        help="Target .NET version (auto-detected from installed SDKs if omitted)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose tool output",
    )

    args = parser.parse_args()

    if not os.path.isdir(args.project):
        print(f"❌ Project path does not exist: {args.project}")
        sys.exit(1)

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("❌ DEEPSEEK_API_KEY environment variable not set.")
        print("   Export it with: export DEEPSEEK_API_KEY=your_key_here")
        sys.exit(1)

    from_tfm = normalize_tfm(args.from_version) if args.from_version else detect_source_tfm(args.project)
    to_tfm = normalize_tfm(args.to_version) if args.to_version else detect_target_tfm()

    print(f"\n🔎 Detected migration path: {from_tfm} ──▶ {to_tfm}")

    if from_tfm == to_tfm:
        print(f"⚠️  Project is already targeting {to_tfm}. Pass --to-version to override.")
        sys.exit(0)

    print("\n⚠️  IMPORTANT: Make sure you have a git commit or backup before running!")
    print("   This agent will modify your files directly.\n")
    confirm = input("   Type 'yes' to continue: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        sys.exit(0)

    run_phased_migration(args.project, from_tfm, to_tfm, not args.quiet)


if __name__ == "__main__":
    main()
