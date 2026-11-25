"""
agent.py — The core ReAct loop.
"""
import subprocess
import os
import time
import json
from tools import TOOL_DEFINITIONS, execute_tool
from prompts import build_system_prompt, build_initial_message, PHASE_NAMES
from inventory import run_inventory, inventory_to_prompt, run_test_inventory
from packages import apply_package_updates, resolve_package_versions
from openai import OpenAI
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageFunctionToolCall
)


PHASE_MAX_TOKENS = {
    1: 4096,
    2: 16384,
    3: 16384,
    4: 4096,
}

PHASE_MAX_ITERATIONS = {
    1: 15,
    2: 20,
    3: 40,
    4: 15,
}

#different models for different phases
PHASE_MODELS = {
    1: "deepseek-flash",   # mechanical csproj edits
    2: "deepseek-v4-pro",  # startup modernization
    3: "deepseek-v4-pro",  # complex source fixes
    4: "deepseek-flash",   # test fixes
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
    resolved_versions = resolve_package_versions(csproj_files, target_version)

    if start_phase <= 1:
        print("\n📦 Applying package updates in Python...")
        apply_package_updates(csproj_files, resolved_versions, target_version)

        print("\n🔨 Verifying build after package updates...")
        build_result = subprocess.run(
            "dotnet build",
            shell=True, cwd=project_path,
            capture_output=True, text=True
        )
        build_ok = build_result.returncode == 0
        print(f"   Build: {'✅ green' if build_ok else '❌ errors remain'}")

        if build_ok:
            subprocess.run(
                f'cd "{project_path}" && git add -A && git commit -m "migration: package_updates"',
                shell=True
            )
            start_phase = 2

    for phase in range(start_phase, len(PHASE_NAMES) + 1):
        test_failures = None
        print(f"\n{'='*60}\n  PHASE {phase}: {PHASE_NAMES[phase]}\n{'='*60}")

        # agent.py — in run_phased_migration, phase 4 build check
 
        if phase == 4:
            print("🔨 Verifying build before running tests...")
            build_result = subprocess.run(
                "dotnet build", shell=True, cwd=project_path, capture_output=True, text=True
            )
            if build_result.returncode != 0:
                print("❌ Build not green — running phase 3 recovery before tests")
                errors = build_result.stdout + build_result.stderr
                recovery_message = f"""The build has errors that were not fixed in the previous phase. Fix these errors then say PHASE COMPLETE:
        {errors[-2000:]}
        """
                recovery_prompt = build_system_prompt(
                    source_version, target_version, project_path,
                    phase=3,
                    inventory_context=inventory_context,
                    resolved_versions=resolved_versions,
                )
                completed = run_agent(
                    system_prompt=recovery_prompt,
                    initial_message=recovery_message,
                    verbose=verbose,
                    max_tokens=PHASE_MAX_TOKENS[3],
                    max_iterations=PHASE_MAX_ITERATIONS[3],
                    model=PHASE_MODELS[3],
                )
                if not completed:
                    print("⚠️ Could not fix build errors. Fix manually then rerun from phase 4.")
                    break
                subprocess.run(
                    f'cd {project_path} && git add -A && git commit -m "migration: source_updates_followup"',
                    shell=True,
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

def run_agent(
    system_prompt: str,
    initial_message: str,
    verbose: bool = True,
    max_tokens: int = 4096,
    max_iterations: int = 15,
    model: str = "deepseek-chat"
) -> bool:
    """
    Main agent loop using the OpenAI-compatible DeepSeek API.
    """
    client = OpenAI(
        api_key=os.environ.get("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
        max_retries=5,
    )

    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": initial_message}
    ]

    for iteration in range(1, max_iterations + 1):
        print(f"\n── Iteration {iteration} {'─'*40}")

        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            tools=TOOL_DEFINITIONS,
            messages=messages,
            extra_body={"thinking": {"type": "disabled"}},
        )
        time.sleep(2)

        if response.usage:
            print(
                f"Tokens: input={response.usage.prompt_tokens}, "
                f"cache_hit={getattr(response.usage, 'prompt_cache_hit_tokens', 0)}, "
                f"output={response.usage.completion_tokens}"
            )

        choice = response.choices[0]
        message = choice.message

        assistant_msg: ChatCompletionAssistantMessageParam = {
            "role": "assistant",
            "content": message.content,
        }
        function_calls = [
            tc for tc in (message.tool_calls or [])
            if isinstance(tc, ChatCompletionMessageFunctionToolCall)
        ]

        if function_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in function_calls
            ]

        messages.append(assistant_msg)

        phase_complete = False

        if message.content and message.content.strip():
            if verbose:
                print(f"\n🤖 DeepSeek: {message.content.strip()}")
            if "PHASE COMPLETE" in message.content:
                phase_complete = True

        if function_calls:
            last_result = ""
            ran_build = False
            for tool_call in function_calls:
                tool_name = tool_call.function.name
                try:
                    tool_input = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError as e:
                    print(f"\n⚠️ Malformed tool JSON (likely truncated): {e}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": (
                            f"ERROR: Your tool call arguments were invalid/truncated JSON ({e}). "
                            "Use smaller edits with replace_in_file instead of rewriting an entire large file in one call."
                        ),
                    })
                    continue

                if tool_name == "run_command" and "dotnet build" in tool_input.get("command", ""):
                    ran_build = True                

                print(f"\n🔧 Tool: {tool_name}")
                if verbose:
                    for k, v in tool_input.items():
                        val_preview = str(v)[:120] + "..." if len(str(v)) > 120 else str(v)
                        print(f"   {k}: {val_preview}")

                result = execute_tool(tool_name, tool_input)
                compressed = compress_tool_result(tool_name, result)
                last_result = compressed

                if verbose:
                    result_preview = result[:300] + "\n..." if len(result) > 300 else result
                    print(f"\n   Result:\n{result_preview}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": compressed,
                })

            if "✅ SUCCESS" in last_result and "0 Error" in last_result and ran_build:
                print("\n✅ Build green — treating as phase complete")
                return True
            continue


        # No tools used — check if done
        if phase_complete or choice.finish_reason == "stop":
            return True

    return False



def compress_tool_result(tool_name: str, result: str) -> str:
    if tool_name == "run_command":
        lines = result.splitlines()
        if "Build FAILED" in result or ": error " in result:
            error_lines = [l for l in lines if ": error " in l or "Build FAILED" in l]
            # Deduplicate identical errors across target frameworks
            seen = set()
            unique_errors = [x for x in error_lines if not (x in seen or seen.add(x))]
            return "\n".join(unique_errors[:40])
        limit = 60 if "dotnet test" in result else 25
        if len(lines) > limit:
            return f"[{len(lines)-limit} lines truncated]\n" + "\n".join(lines[-limit:])
    if tool_name == "read_file":
        lines = result.splitlines()
        if len(lines) > 100:
            return "\n".join(lines[:100]) + f"\n[{len(lines)-100} more lines omitted]"
    return result
