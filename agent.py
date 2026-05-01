"""
agent.py — The core ReAct loop.
"""
import subprocess
import anthropic
from anthropic.types import MessageParam
from tools import TOOL_DEFINITIONS, execute_tool
from prompts import build_system_prompt, build_initial_message, PHASE_NAMES
import time
from inventory import run_inventory, inventory_to_prompt, run_test_inventory
from packages import apply_package_updates, resolve_package_versions

PHASE_MAX_TOKENS = {
    1: 2048,
    2: 8096,
    3: 4096,
    4: 2048,
}

PHASE_MAX_ITERATIONS = {
    1: 15,
    2: 20,
    3: 15,
    4: 10,
}

#different models for different phases
PHASE_MODELS = {
    1: "claude-haiku-4-5-20251001",  # mechanical csproj edits
    2: "claude-sonnet-4-5",           # startup modernization
    3: "claude-opus-4-5",             # complex source fixes — use the best model
    4: "claude-sonnet-4-5",           # test fixes
}


def get_completed_phases(project_path: str) -> set[int]:
    """Check git log to find which phases already committed."""
    result = subprocess.run(
        "git log --oneline",
        shell=True, cwd=project_path,
        capture_output=True, text=True
    )
    completed = set()
    for phase, name in PHASE_NAMES.items():
        if f"migration: {name}" in result.stdout:
            completed.add(phase)
            print(f"   ✅ Phase {phase} ({name}) already committed — skipping")
    return completed


def run_phased_migration(project_path, source_version, target_version,
                         verbose=True, start_phase=None):

    # Check what's already done
    print("🔍 Checking git history for completed phases...")
    completed_phases = get_completed_phases(project_path)

    if len(completed_phases) == len(PHASE_NAMES):
        print("\n✅ All phases already committed. Migration is complete!")
        print("   If you want to re-run, reset the git commits first:")
        print(f"   git reset HEAD~{len(PHASE_NAMES)}")
        return

    # Determine start_phase — parameter takes priority, otherwise resume from git
    if start_phase is None:
        if completed_phases:
            start_phase = max(completed_phases) + 1
            print(f"   Resuming from phase {start_phase}")
        else:
            start_phase = 1

    # Run inventory once in Python — no agent needed
    inventory = run_inventory(project_path)
    inventory_context = inventory_to_prompt(inventory)

    # Resolve and apply package updates in Python
    csproj_files = list(inventory["csproj_files"].keys())
    print("🔍 Pre-resolving NuGet package versions...")
    resolved_versions = resolve_package_versions(csproj_files)

    if start_phase <= 1:
        print("\n📦 Applying package updates in Python...")
        apply_package_updates(csproj_files, resolved_versions)

        print("\n🔨 Verifying build after package updates...")
        build_result = subprocess.run(
            "dotnet build",
            shell=True, cwd=project_path,
            capture_output=True, text=True
        )
        build_ok = build_result.returncode == 0
        print(f"   Build: {'✅ green' if build_ok else '❌ errors remain'}")

        subprocess.run(
            f'cd {project_path} && git add -A && git commit -m "migration: package_updates"',
            shell=True
        )

        # Skip to phase 2 if build is green, otherwise agent fixes phase 1
        if build_ok and start_phase == 1:
            start_phase = 2

    for phase in range(start_phase, len(PHASE_NAMES) + 1):
        test_failures = None
        print(f"\n{'='*60}\n  PHASE {phase}: {PHASE_NAMES[phase]}\n{'='*60}")

        # agent.py — in run_phased_migration, phase 4 build check
        if phase == 4:
            print("🔨 Verifying build before running tests...")
            build_result = subprocess.run(
                "dotnet build",
                shell=True, cwd=project_path,
                capture_output=True, text=True
            )
            if build_result.returncode != 0:
                print("❌ Build not green — sending back to phase 3 to fix remaining errors")
                # Run phase 3 again with the build errors as context
                errors = build_result.stdout + build_result.stderr
                phase = 3
                initial_message = f"""The build has errors that were not fixed in the previous phase.
        Fix these errors then say PHASE COMPLETE:

        {errors[-2000:]}
        """
                system_prompt = build_system_prompt(
                    source_version, target_version, project_path,
                    phase=3,
                    inventory_context=inventory_context,
                    resolved_versions=resolved_versions,
                )
                completed = run_agent(
                    system_prompt=system_prompt,
                    initial_message=initial_message,
                    verbose=verbose,
                    max_tokens=PHASE_MAX_TOKENS[3],
                    max_iterations=PHASE_MAX_ITERATIONS[3],
                    model=PHASE_MODELS[3]
                )
                if not completed:
                    print("⚠️  Could not fix build errors. Fix manually then rerun from phase 4.")
                    break
                subprocess.run(
                    f'cd {project_path} && git add -A && git commit -m "migration: source_updates_followup"',
                    shell=True
                )
            # Now continue to phase 4
            print("🧪 Pre-running tests to identify failures...")
            test_failures = run_test_inventory(project_path)
            print(test_failures[:500])

        initial_message = build_initial_message(
            project_path, source_version, target_version, phase
        )
        system_prompt = build_system_prompt(
            source_version, target_version, project_path,
            phase=phase,
            inventory_context=inventory_context,
            resolved_versions=resolved_versions,
            test_failures=test_failures
        )

        completed = run_agent(
            system_prompt=system_prompt,
            initial_message=initial_message,
            verbose=verbose,
            max_tokens=PHASE_MAX_TOKENS[phase],
            max_iterations=PHASE_MAX_ITERATIONS[phase],
            model=PHASE_MODELS[phase]
        )

        if completed:
            commit_result = subprocess.run(
                f'cd {project_path} && git add -A && git commit -m "migration: {PHASE_NAMES[phase]}"',
                shell=True, capture_output=True, text=True
            )
            if commit_result.returncode == 0 or "nothing to commit" in commit_result.stdout:
                print(f"✅ Phase {phase} complete and committed")
            else:
                print(f"⚠️  Commit failed: {commit_result.stdout}")
        else:
            print(f"⚠️  Phase {phase} ({PHASE_NAMES[phase]}) did not complete.")
            print(f"   Fix manually then rerun from phase {phase}.")
            break


def run_agent(system_prompt: str, initial_message: str,
              verbose: bool = True, max_tokens: int = 4096,
              max_iterations: int = 15,
              model: str = "claude-sonnet-4-5") -> bool:
    """
    Main agent loop. Runs until Claude decides the migration is done
    or the max iteration limit is hit.
    """
    client = anthropic.Anthropic(max_retries=10)

    messages: list[MessageParam] = [
        {"role": "user", "content": initial_message}
    ]

    for iteration in range(1, max_iterations + 1):
        print(f"\n── Iteration {iteration} {'─'*40}")

        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            tools=TOOL_DEFINITIONS,
            messages=messages
        )
        time.sleep(5)

        usage = response.usage
        print(f"Tokens: input={usage.input_tokens}, "
              f"cache_read={getattr(usage, 'cache_read_input_tokens', 0)}, "
              f"cache_created={getattr(usage, 'cache_creation_input_tokens', 0)}, "
              f"output={usage.output_tokens}")

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        phase_complete = False

        for block in response.content:
            if block.type == "text":
                if verbose and block.text.strip():
                    print(f"\n🤖 Claude: {block.text.strip()}")
                if "PHASE COMPLETE" in block.text:
                    phase_complete = True

            elif block.type == "tool_use":
                tool_name = block.name
                tool_input = block.input

                print(f"\n🔧 Tool: {tool_name}")
                if verbose:
                    for k, v in tool_input.items():
                        val_preview = str(v)[:120] + "..." if len(str(v)) > 120 else str(v)
                        print(f"   {k}: {val_preview}")

                result = execute_tool(tool_name, tool_input)
                compressed = compress_tool_result(block.name, result)

                if verbose:
                    result_preview = result[:300] + "\n..." if len(result) > 300 else result
                    print(f"\n   Result:\n{result_preview}")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": compressed
                })

        # Always append tool results before any early exit
        if tool_results:
            messages.append({"role": "user", "content": tool_results})

            last_result = tool_results[-1]["content"]
            last_was_build = any(
                "dotnet build" in str(msg.get("content", ""))
                for msg in messages[-4:-2]
                if isinstance(msg, dict)
            )
            if "✅ SUCCESS" in last_result and "0 Error" in last_result and last_was_build:
                print("\n✅ Build green — treating as phase complete")
                return True
            continue

        # No tools used — check if done
        if phase_complete or response.stop_reason == "end_turn":
            return True

    return False


def compress_tool_result(tool_name: str, result: str) -> str:
    if tool_name == "run_command":
        lines = result.splitlines()
        limit = 60 if "dotnet test" in result else 20
        if len(lines) > limit:
            return f"[{len(lines)-limit} lines truncated]\n" + "\n".join(lines[-limit:])
    if tool_name == "read_file":
        lines = result.splitlines()
        if len(lines) > 100:
            return "\n".join(lines[:100]) + f"\n[{len(lines)-100} more lines omitted]"
    return result