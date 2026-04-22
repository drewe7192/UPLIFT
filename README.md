# UPLIFT

An agentic .NET migration tool powered by Claude. UPLIFT automates the process of upgrading .NET projects to newer framework versions by running a phased, self-verifying migration pipeline.

## What it does

UPLIFT takes a .NET solution, figures out everything that needs to change, and fixes it — package versions, deprecated APIs, startup modernization, and test verification — committing progress after each phase so you never lose work.

## How it works

Rather than one long agent session that burns context and hits iteration limits, UPLIFT splits the migration into focused phases. Each phase gets a fresh context window, a single narrow goal, and a hard iteration cap.

```
Python (free)          Agent (paid)
─────────────          ────────────
run_inventory()   →    Phase 1: Package updates      (~15 iterations)
resolve_versions() →   Phase 2: Startup modernization (~15 iterations)
                  →    Phase 3: Source file fixes      (~15 iterations)
                  →    Phase 4: Tests                  (~10 iterations)
```

Data gathering (file reads, grep searches, NuGet version lookups) happens in Python before any agent runs. The agent only touches things that require reasoning — what to change and how.

## Project structure

```
uplift/
├── agent.py        # Phased agent loop, checkpoint logic, token logging
├── tools.py        # Tool definitions and implementations (file I/O, shell)
├── prompts.py      # Per-phase system prompts
├── runner.py       # CLI entry point
└── README.md
```

## Installation

Requires Python 3.12+ and the .NET 10 SDK.

```bash
git clone https://github.com/yourname/uplift
cd uplift
pip install anthropic
export ANTHROPIC_API_KEY=sk-...
```

## Usage

```bash
python runner.py --project /path/to/your/solution \
                 --from "NET 3.1" \
                 --to "NET 10"
```

To run quietly (suppress per-iteration output):

```bash
python runner.py --project /path/to/solution --from "NET 8" --to "NET 10" --quiet
```

To start from a specific phase (e.g. if phase 2 failed and you fixed it manually):

```bash
python runner.py --project /path/to/solution --from "NET 8" --to "NET 10" --start-phase 3
```

## Cost

Typical migration of a mid-size solution (10 projects, ~50 source files):

| Approach | Cost |
|---|---|
| Single session (old) | ~$2–4 |
| Phased with inventory pre-computed | ~$0.30–0.60 |

The savings come from three things: shorter context windows per phase, pre-computed inventory (no agent search iterations), and pre-resolved NuGet versions (no agent package lookup iterations).

## How phases work

### Pre-flight (Python, no agent)
Before any agent runs, UPLIFT:
- Runs `dotnet build` and records all errors
- Greps source for deprecated APIs (IdentityServer4, Newtonsoft, Swashbuckle, etc.)
- Reads all `.csproj` files
- Queries NuGet for the latest compatible version of every referenced package

This data is injected directly into the agent's context so it never wastes iterations gathering information.

### Phase 1 — Package updates
Only `.csproj` files are touched. TargetFramework and NuGet versions are updated using the pre-resolved version table. Build must be green before the phase commits.

### Phase 2 — Startup modernization
`Startup.cs` is merged into `Program.cs` using the `WebApplication.CreateBuilder()` pattern. Every service registration from the old `Startup.cs` is accounted for. Build must be green before commit.

### Phase 3 — Source file fixes
Remaining `.cs` file errors are fixed one file at a time, with a build check after each change. Common fixes: IdentityServer4 → Duende namespaces, Newtonsoft → System.Text.Json, FluentValidation API changes, CsvHelper API changes.

### Phase 4 — Tests
`dotnet test` is run and any failures are fixed.

Each phase ends with a `git commit` so progress is always saved. If a phase fails, you can fix the issue manually and rerun from that phase with `--start-phase`.

## Hard rules the agent follows

- Never modify `.cs` files until `dotnet build` is green on updated `.csproj` files
- Never guess NuGet versions — pre-resolved versions are provided
- If `dotnet build` fails twice on the same issue, stop and report rather than loop
- Run `dotnet build` after every file change
- Commit at the end of every phase

## Tool surface

The agent has access to six tools:

| Tool | Purpose |
|---|---|
| `list_directory` | Explore project structure |
| `read_file` | Read a single file |
| `read_multiple_files` | Read several files in one call |
| `replace_in_file` | Surgical string replacement (preferred over full rewrites) |
| `write_file` | Write or overwrite a file |
| `search_in_files` | Grep across the codebase |
| `run_command` | Run shell commands (`dotnet build`, `dotnet test`, `git`) |

`run_command` automatically strips pipes and filters output to the last 100 lines so build errors are always visible without noise.

## Limitations

- Requires a clean `git` working tree before running (uncommitted changes may be overwritten)
- IdentityServer / Duende migrations may need manual review if your auth configuration is complex
- SPA projects (Angular/React inside the solution) are not touched — only the .NET backend
- Does not upgrade Entity Framework migrations — those should be handled separately