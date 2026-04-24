"""
agent.py — The core ReAct loop.
This is where Claude is called repeatedly, tools are executed,
and results are fed back until the migration is complete.
"""
import subprocess

import anthropic
from anthropic.types import MessageParam
from tools import TOOL_DEFINITIONS, execute_tool
from prompts import build_system_prompt, build_initial_message, PHASE_NAMES, resolve_package_versions
import time
from inventory import run_inventory, inventory_to_prompt
from packages import apply_package_updates

def run_phased_migration(project_path, source_version, target_version, verbose=True):
    # Run inventory once in Python — no agent needed
    inventory = run_inventory(project_path)
    inventory_context = inventory_to_prompt(inventory)

    # Resolve package versions before phase 1
    csproj_files = list(inventory["csproj_files"].keys())
    print("🔍 Pre-resolving NuGet package versions...")
    resolved_versions = resolve_package_versions(csproj_files)

    # Apply package updates in Python — no agent needed for phase 1
    print("\n📦 Applying package updates in Python...")
    apply_package_updates(csproj_files, resolved_versions)

    # Verify build after Python applies updates
    print("\n🔨 Verifying build after package updates...")
    result = subprocess.run(
        "dotnet build",
        shell=True, cwd=project_path,
        capture_output=True, text=True
    )
    build_ok = result.returncode == 0
    print(f"   Build: {'✅ green' if build_ok else '❌ errors remain'}")

    # Commit what Python did
    subprocess.run(
        f'cd {project_path} && git add -A && git commit -m "migration: package_updates"',
        shell=True
    )

    # Phase 1 is done by Python — agent only runs phases 2 onwards
    # If build still broken after Python, run agent for phase 1 to fix conflicts
    start_phase = 2 if build_ok else 1

    for phase in range(start_phase, len(PHASE_NAMES) + 1):
        print(f"\n{'='*60}\n  PHASE {phase}: {PHASE_NAMES[phase]}\n{'='*60}")

        initial_message = build_initial_message(project_path, source_version, target_version, phase)

        system_prompt = build_system_prompt(
            source_version, target_version, project_path,
            phase=phase,
            inventory_context=inventory_context,
            resolved_versions=resolved_versions
        )

        completed = run_agent(
            system_prompt=system_prompt,
            initial_message=initial_message,
            verbose=verbose
        )

        if completed:
            subprocess.run(
                f'cd {project_path} && git add -A && git commit -m "migration: {PHASE_NAMES[phase]}"',
                shell=True
            )
            print(f"✅ Phase {phase} complete and committed")
        else:
            print(f"⚠️  Phase {phase} ({PHASE_NAMES[phase]}) did not complete.")
            print(f"   Fix manually then rerun from phase {phase}.")
            break



def run_agent(system_prompt: str, initial_message: str, verbose: bool = True):
    """
    Main agent loop. Runs until Claude decides the migration is done
    or the max iteration limit is hit.
    """
    client = anthropic.Anthropic(max_retries=10)  # reads ANTHROPIC_API_KEY from environment

    # Conversation history — grows with each turn
    messages: list[MessageParam] = [
        {"role": "user", "content": initial_message}
    ]

    for iteration in range(1, 16): 
        print(f"\n── Iteration {iteration} {'─'*40}")

        # Call Claude
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            system=system_prompt,
            tools=TOOL_DEFINITIONS,
            messages=messages
        )
        time.sleep(5)

        # log to check token usage
        usage = response.usage
        print(f"Tokens: input={usage.input_tokens}, "
              f"cache_read={getattr(usage, 'cache_read_input_tokens', 0)}, "
              f"cache_created={getattr(usage, 'cache_creation_input_tokens', 0)}, "
              f"output={usage.output_tokens}")

        # Add Claude's response to history
        messages.append({"role": "assistant", "content": response.content})

        # Process each block in the response
        tool_results = []

        phase_complete = False

        for block in response.content:
            # Text block — Claude explaining what it's doing
            if block.type == "text":
                if verbose and block.text.strip():
                    print(f"\n🤖 Claude: {block.text.strip()}")
                # Detect completion signal
                if "PHASE COMPLETE" in block.text:
                    phase_complete = True

            # Tool use block — Claude wants to call a tool
            elif block.type == "tool_use":
                tool_name = block.name
                tool_input = block.input

                print(f"\n🔧 Tool: {tool_name}")
                if verbose:
                    # Print key args without flooding the console
                    for k, v in tool_input.items():
                        val_preview = str(v)[:120] + "..." if len(str(v)) > 120 else str(v)
                        print(f"   {k}: {val_preview}")

                # Actually run the tool
                result = execute_tool(tool_name, tool_input)
                # To prevent passing entire conversation history on every iteration; helps with token usage
                compressed = compress_tool_result(block.name, result)

                if verbose:
                    result_preview = result[:300] + "\n..." if len(result) > 300 else result
                    print(f"\n   Result:\n{result_preview}")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": compressed
                })

        # If Claude used tools, feed results back and continue
        if tool_results:
            messages.append({"role": "user", "content": tool_results})
            continue

        if phase_complete or response.stop_reason == "end_turn":
            return True  # phase completed

    return False # hit iteration limit without completing

def compress_tool_result(tool_name: str, result: str) -> str:
    """Keep tool results small so history doesnt balloon."""
    if tool_name == "run_command":
        # Only keep last 20 lines - full output already seen by agent
        lines = result.splitlines()
        if len(lines) > 20:
            return f"[{len(lines) -20} lines omitted]\n" + "\n".join(lines[-20:])
        
    if tool_name == "read_file":
        # Cap file reads at 100 lines in history
        lines = result.splitlines()
        if len(lines) > 100:
            return "\n".join(lines[:100]) + f"\n[{len(lines) -100} more lines omitted]"
    return result