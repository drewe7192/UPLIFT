import os
import glob
import subprocess
import re

def run_inventory(project_path: str) -> dict:
    """Run full inventory before any agent phase starts."""
    
    print("🔍 Running inventory...")
    inventory = {}

    # 1. dotnet build — get baseline errors
    result = subprocess.run(
        "dotnet build",
        shell=True, cwd=project_path,
        capture_output=True, text=True
    )
    errors = re.findall(r"error CS\d+.*", result.stdout + result.stderr)
    inventory["build_errors"] = errors
    inventory["error_count"] = len(errors)
    print(f"   Build errors: {len(errors)}")

    # 2. Read all csproj files
    csproj_files = glob.glob(f"{project_path}/**/*.csproj", recursive=True)
    inventory["csproj_files"] = {}
    for path in csproj_files:
        with open(path) as f:
            inventory["csproj_files"][path] = f.read()
    print(f"   Found {len(csproj_files)} .csproj files")

    # 3. Search for deprecated APIs
    deprecated_patterns = [
        "IdentityServer4", "NSwag", "Swashbuckle", "Newtonsoft",
        "PropertyValidatorContext", "IHostingEnvironment",
        "AddNewtonsoftJson", "UseSwaggerUi3", "AddOpenApiDocument"
    ]
    inventory["deprecated_apis"] = {}
    for pattern in deprecated_patterns:
        result = subprocess.run(
            f'grep -rn "{pattern}" {project_path}/Src --include="*.cs" --include="*.csproj"',
            shell=True, capture_output=True, text=True
        )
        if result.stdout.strip():
            inventory["deprecated_apis"][pattern] = result.stdout.strip()
    print(f"   Deprecated APIs found: {list(inventory['deprecated_apis'].keys())}")

    # 4. Read Program.cs and Startup.cs
    for filename in ["Program.cs", "Startup.cs"]:
        matches = glob.glob(f"{project_path}/**/{filename}", recursive=True)
        if matches:
            with open(matches[0]) as f:
                inventory[filename] = f.read()
            inventory[f"{filename}_path"] = matches[0]
    
    return inventory


def inventory_to_prompt(inventory: dict) -> str:
    """Format inventory as context for the agent."""
    
    lines = ["## PROJECT INVENTORY (pre-computed, do not re-run these searches)\n"]

    lines.append(f"### Build Status: {inventory['error_count']} errors")
    for err in inventory["build_errors"]:
        lines.append(f"- {err}")

    lines.append("\n### Deprecated APIs Found")
    if inventory["deprecated_apis"]:
        for pattern, matches in inventory["deprecated_apis"].items():
            lines.append(f"\n**{pattern}:**\n```\n{matches}\n```")
    else:
        lines.append("None found.")

    lines.append("\n### .csproj Files")
    for path, content in inventory["csproj_files"].items():
        lines.append(f"\n**{path}:**\n```xml\n{content}\n```")

    if "Startup.cs" in inventory:
        lines.append(f"\n### Startup.cs ({inventory['Startup.cs_path']})")
        lines.append(f"```csharp\n{inventory['Startup.cs']}\n```")

    if "Program.cs" in inventory:
        lines.append(f"\n### Program.cs ({inventory['Program.cs_path']})")
        lines.append(f"```csharp\n{inventory['Program.cs']}\n```")

    return "\n".join(lines)

def run_test_inventory(project_path: str, max_chars: int = 3000) -> str:
    """Run tests once and return structured failures. Free — no agent needed."""
    result = subprocess.run(
        "dotnet test --logger \"trx;LogFileName=/tmp/uplift_results.trx\"",
        shell=True, cwd=project_path,
        capture_output=True, text=True, timeout=300
    )
    
    if result.returncode == 0:
        return "All tests passing."
    
    try:
        with open("/tmp/uplift_results.trx") as f:
            content = f.read()
        import re
        failures = re.findall(
            r'testName="([^"]+)"[^>]*outcome="Failed".*?<Message>(.*?)</Message>',
            content, re.DOTALL
        )
        lines = [f"FAILED: {name}\n  {msg.strip()[:200]}" 
                 for name, msg in failures]
        output = f"{len(failures)} test failures:\n\n" + "\n\n".join(lines)
    except Exception:
        output = result.stdout[-3000:] + result.stderr[-1000:]

    return output[:max_chars]