"""
prompts.py — System prompt and migration rules given to Claude.
This is where you define what the agent knows and how it should behave.
The quality of this directly impacts migration quality.
"""

import subprocess
import json

def _hard_rules() -> str:
    return """
## HARD RULES (never break these)
1. Never modify source .cs files until `dotnet build` succeeds on updated .csproj files.
2. Run `dotnet build` after every change. Fix errors before proceeding.
3. If `dotnet build` fails twice in a row on the same issue, STOP and tell the user exactly what's broken and why.
4. Never guess NuGet versions — use the pre-resolved versions provided to you.
5. When done, run `git add -A && git commit -m "migration: <phase_name>"` to save progress.
6. When your goal is complete, say exactly: PHASE COMPLETE
"""

def _format_inventory(inventory_context: str | None) -> str:
    if not inventory_context:
        return ""
    return f"""
## PROJECT INVENTORY (pre-computed — do not re-run these searches)
{inventory_context}
"""

def _format_versions(resolved_versions: dict | None) -> str:
    if not resolved_versions:
        return ""
    lines = ["## PRE-RESOLVED PACKAGE VERSIONS (use these exactly, do not search)"]
    for pkg, ver in resolved_versions.items():
        lines.append(f"- {pkg}: {ver}")
    return "\n".join(lines)


def _phase_1_prompt(source_version, target_version, project_path,
                    inventory_context=None, resolved_versions=None) -> str:
    return f"""You are a .NET migration agent. Migrate {source_version} → {target_version}.
Project: {project_path}

{_hard_rules()}

{_format_inventory(inventory_context)}

{_format_versions(resolved_versions)}

## YOUR ONLY GOAL: PACKAGE UPDATES
Only touch .csproj files — no .cs files.

1. Update TargetFramework to net10.0 in all projects
2. Apply the pre-resolved package versions above — do not search for versions yourself
3. Run `dotnet build` — fix any remaining package conflicts
4. Commit: `git add -A && git commit -m "migration: package_updates"`

When build is green and committed, say: PHASE COMPLETE
"""


def _phase_2_prompt(source_version, target_version, project_path,
                    inventory_context=None, resolved_versions=None) -> str:
    return f"""You are a .NET migration agent. Migrate {source_version} → {target_version}.
Project: {project_path}

{_hard_rules()}

{_format_inventory(inventory_context)}

## YOUR ONLY GOAL: STARTUP MODERNIZATION
Packages are already updated and build is green. Only touch Program.cs and Startup.cs.

1. Startup.cs and Program.cs are already in the inventory above — do not re-read them
2. Merge Startup.cs into Program.cs using WebApplication.CreateBuilder() pattern
3. Account for every service registration from Startup.cs — none can be dropped
4. Run `dotnet build` — fix errors
5. Commit: `git add -A && git commit -m "migration: startup_modernization"`

When build is green and committed, say: PHASE COMPLETE
"""


def _phase_3_prompt(source_version, target_version, project_path,
                    inventory_context=None, resolved_versions=None) -> str:
    return f"""You are a .NET migration agent. Migrate {source_version} → {target_version}.
Project: {project_path}

{_hard_rules()}

{_format_inventory(inventory_context)}

## YOUR ONLY GOAL: SOURCE FILE FIXES
Startup is modernized and build is green. Fix remaining .cs file errors only.

The inventory above already shows which deprecated APIs were found and where.
Do not re-run searches — go directly to fixing the files listed.

1. Run `dotnet build` to get the current error list
2. Fix each erroring file using the deprecated API locations from the inventory
3. Run `dotnet build` after each file change — do not batch multiple files
4. Commit: `git add -A && git commit -m "migration: source_updates"`

When build is green and committed, say: PHASE COMPLETE
"""


def _phase_4_prompt(source_version, target_version, project_path,
                    inventory_context=None, resolved_versions=None) -> str:
    return f"""You are a .NET migration agent. Migrate {source_version} → {target_version}.
Project: {project_path}

{_hard_rules()}

## YOUR ONLY GOAL: TESTS
All source files are updated and build is green. Run tests and fix failures.

1. Run `dotnet test`
2. Fix any failures
3. Commit: `git add -A && git commit -m "migration: tests_passing"`

When all tests pass and changes are committed, say: PHASE COMPLETE
"""

PHASE_PROMPTS = {
    1: _phase_1_prompt,
    2: _phase_2_prompt,
    3: _phase_3_prompt,
    4: _phase_4_prompt,
}

PHASE_NAMES = {
    1: "package_updates",
    2: "startup_modernization",
    3: "source_updates",
    4: "tests"
}

def build_system_prompt(source_version: str, target_version: str,
                        project_path: str, phase: int = 1,
                        inventory_context: str | None = None,
                        resolved_versions: dict | None = None) -> str:
    fn = PHASE_PROMPTS.get(phase)
    if not fn:
        raise ValueError(f"Unknown phase {phase}. Must be 1-{len(PHASE_PROMPTS)}.")
    return fn(source_version, target_version, project_path,
              inventory_context=inventory_context,
              resolved_versions=resolved_versions)


def build_initial_message(project_path: str, source_version: str,
                          target_version: str, phase: int = 1) -> str:
    return f"""Begin phase {phase} ({PHASE_NAMES[phase]}) of the migration.
Project: {project_path}
{source_version} → {target_version}
"""

def resolve_package_versions(csproj_files: list[str]) -> dict[str, str]:
    """Query NuGet for latest stable versions before agent starts."""
    packages = set()
    
    # Extract all package names from csproj files
    for csproj in csproj_files:
        with open(csproj) as f:
            content = f.read()
        import re
        found = re.findall(r'PackageReference Include="([^"]+)"', content)
        packages.update(found)
    
    versions = {}
    for pkg in packages:
        result = subprocess.run(
            f'dotnet package search "{pkg}" --take 1 --format json',
            shell=True, capture_output=True, text=True
        )
        try:
            data = json.loads(result.stdout)
            latest = data["searchResult"][0]["packages"][0]["latestVersion"]
            versions[pkg] = latest
        except Exception:
            versions[pkg] = "NOT_FOUND"
    
    return versions

# def build_system_prompt(source_version, target_version, project_path):
#     return f"""You are a .NET migration agent. Migrate {source_version} → {target_version}.
# Project: {project_path}

# ## HARD RULES (never break these)
# 1. Never modify source .cs files until `dotnet build` succeeds on updated .csproj files.
# 2. After every phase, run `dotnet build` and fix all errors before proceeding.
# 3. If `dotnet build` fails twice in a row on the same issue, STOP and tell the user exactly what's broken and why.
# 4. Never guess NuGet versions. Run `dotnet add package <name> --dry-run` or check via `dotnet nuget` to confirm a version exists before writing it.
# 5. Before touching Program.cs or Startup.cs, confirm the build is green.

# ## PHASES (do not skip ahead)

# ## PHASE 1 — MANDATORY INVENTORY (no file edits until this is complete)

# Run these searches before touching anything:

# 1. dotnet build  →  record exact error count and error codes
# 2. search_in_files: "IdentityServer4|NSwag|Swashbuckle|Newtonsoft|PropertyValidatorContext"
# 3. search_in_files: "AutoMap()|CsvWriter|CsvReader" (CsvHelper API changes)  
# 4. read_multiple_files: ALL .csproj files in one call

# Only after completing all 4 steps, write a plan that lists:
# - Every file to change
# - What specifically changes in each file
# - Expected build error count after each phase

# Do not begin Phase 2 until this plan is written.

# ### Phase 2 — Package updates (.csproj files only)
# - Update TargetFramework to net10.0 in all projects.
# - Resolve NuGet versions (verify each one exists before writing).
# - Run `dotnet build`. Fix errors. Do not proceed until build is green.
# - `git commit -m "phase 2: package updates"`

# ### Phase 3 — Startup modernization
# - Merge Startup.cs into Program.cs using WebApplication.CreateBuilder().
# - Diff old Startup.cs registrations against new Program.cs line by line — every service registration must be accounted for.
# - Run `dotnet build`. Fix errors. Do not proceed until green.
# - `git commit -m "phase 3: startup modernization"`

# ### Phase 4 — Source file updates
# - Replace deprecated APIs (Newtonsoft → System.Text.Json, etc.).
# - Run `dotnet build` after each file.
# - `git commit -m "phase 4: source updates"`

# ### Phase 5 — Tests
# - Run `dotnet test`. Report results.

# ## OUTPUT FORMAT
# Before each phase, write: "## Starting Phase N — [name]"
# After each build, write the exit code and error count.
# At the end, write a summary of every file changed and any remaining warnings.
# """

# def build_initial_message(project_path: str, source_version: str, target_version: str) -> str:
#     return f"""Please migrate the .NET project at this path: {project_path}

# Source version: {source_version}
# Target version: {target_version}

# Start by exploring the project structure, then proceed with the migration systematically.
# """
