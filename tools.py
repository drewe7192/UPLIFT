"""
tools.py — All the actions the agent can take on your codebase.
Each function here corresponds to a tool definition sent to Claude.
"""

import os
import subprocess
from anthropic.types import ToolParam


# ── Tool Definitions (sent to Claude so it knows what's available) ──────────

TOOL_DEFINITIONS: list[ToolParam] = [
    {
        "name": "list_directory",
        "description": "List files and folders in a directory. Use this to explore the project structure.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "read_file",
        "description": "Read the full contents of a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write (or overwrite) a file with new content. Use for creating new files or completely rewriting small ones.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write"},
                "content": {"type": "string", "description": "Full file content to write"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "replace_in_file",
        "description": "Replace an exact string in a file with a new string. Preferred for surgical edits — less risky than rewriting the whole file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to edit"},
                "old_str": {"type": "string", "description": "Exact string to find (must be unique in the file)"},
                "new_str": {"type": "string", "description": "String to replace it with"}
            },
            "required": ["path", "old_str", "new_str"]
        }
    },
    {
        "name": "search_in_files",
        "description": "Search for a pattern (string or regex) across all files in a directory. Useful for finding deprecated APIs or patterns that need updating.",
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Directory to search in"},
                "pattern": {"type": "string", "description": "String or regex pattern to search for"},
                "file_extension": {"type": "string", "description": "Only search files with this extension, e.g. '.cs' or '.csproj'. Optional."}
            },
            "required": ["directory", "pattern"]
        }
    },
    {
        "name": "run_command",
        "description": "Run a shell command (e.g. 'dotnet build', 'dotnet test'). Use this to verify changes compile and tests pass. Always returns last 100 lines. Never pipe or grep this output — filtering is done for you.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "working_directory": {"type": "string", "description": "Directory to run the command in"}
            },
            "required": ["command", "working_directory"]
        }
    },
    {
        "name": "read_multiple_files",
        "description": "Read several files at once. Use instead of multiple read_file calls when you need to inspect more than one file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["paths"]
        }
    },
    {
        "name": "find_type_in_package",
        "description": "List all public types and extension methods in an installed NuGet package. Use this before calling any method from a package you haven't used before, or when a method name is uncertain.",
        "input_schema": {
            "type": "object", 
            "properties": {
                "package_name": {"type": "string", "description": "Package name e.g. 'Scalar.AspNetCore'"},
                "search_term": {"type": "string", "description": "Optional: filter results to types/methods containing this string"}
            },
            "required": ["package_name"]
        }
    }
]


# ── Tool Implementations (actually do the work) ──────────────────────────────

def list_directory(path: str) -> str:
    try:
        entries = []
        for item in sorted(os.listdir(path)):
            full = os.path.join(path, item)
            prefix = "📁 " if os.path.isdir(full) else "📄 "
            entries.append(f"{prefix}{item}")
        return "\n".join(entries) if entries else "(empty directory)"
    except Exception as e:
        return f"ERROR: {e}"


def read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"ERROR: {e}"


def write_file(path: str, content: str) -> str:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"✅ Written: {path}"
    except Exception as e:
        return f"ERROR: {e}"


def replace_in_file(path: str, old_str: str, new_str: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        count = content.count(old_str)
        if count == 0:
            return f"ERROR: Pattern not found in {path}"
        if count > 1:
            return f"ERROR: Pattern found {count} times in {path} — be more specific to avoid ambiguous replacements"

        new_content = content.replace(old_str, new_str)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return f"✅ Replaced in {path}"
    except Exception as e:
        return f"ERROR: {e}"


def search_in_files(directory: str, pattern: str, file_extension: str = None) -> str:
    try:
        cmd = ["grep", "-rn", pattern, directory]
        if file_extension:
            cmd += ["--include", f"*{file_extension}"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        output = result.stdout.strip()
        return output if output else f"No matches found for '{pattern}'"
    except Exception as e:
        return f"ERROR: {e}"

def run_command(command: str, working_directory: str) -> str:
    MAX_LINES = 100  # errors are at the bottom; no need for full output

    # Strip any pipes the agent adds — we control output formatting
    clean_command = command.split("|")[0].strip()
    
    if clean_command != command.strip():
        pipe_note = f"[Pipe removed: '{command}' → '{clean_command}'. Output filtered automatically.]\n"
    else:
        pipe_note = ""


    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=working_directory,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ}  # inherit PATH so dotnet is found
        )

        # Combine stdout+stderr — dotnet mixes them inconsistently
        combined = (result.stdout + result.stderr).strip()
        lines = combined.splitlines()

        # Keep tail so the agent sees errors, not just restore progress
        if len(lines) > MAX_LINES:
            truncated = len(lines) - MAX_LINES
            visible = "\n".join(lines[-MAX_LINES:])
            output = f"[{truncated} lines truncated]\n{visible}"
        else:
            output = "\n".join(lines)

        # Make success/failure unambiguous — don't bury it
        status = "✅ SUCCESS" if result.returncode == 0 else f"❌ FAILED (exit {result.returncode})"
        return f"{pipe_note}{status}\n\n{output}"

    except subprocess.TimeoutExpired:
        return "❌ FAILED: Command timed out after 120 seconds"
    except Exception as e:
        return f"❌ FAILED: {e}"


# ── Dispatcher: routes tool calls from Claude to the right function ──────────

def execute_tool(tool_name: str, tool_input: dict) -> str:
    """Called by the agent loop when Claude requests a tool."""
    if tool_name == "list_directory":
        return list_directory(tool_input["path"])
    elif tool_name == "read_file":
        return read_file(tool_input["path"])
    elif tool_name == "write_file":
        return write_file(tool_input["path"], tool_input["content"])
    elif tool_name == "replace_in_file":
        return replace_in_file(tool_input["path"], tool_input["old_str"], tool_input["new_str"])
    elif tool_name == "search_in_files":
        return search_in_files(
            tool_input["directory"],
            tool_input["pattern"],
            tool_input.get("file_extension")
        )
    elif tool_name == "run_command":
        return run_command(tool_input["command"], tool_input["working_directory"])
    else:
        return f"ERROR: Unknown tool '{tool_name}'"
    
def read_multiple_files(paths: list[str]) -> str:
    results = []
    for path in paths:
        content = read_file(path)
        results.append(f"=== {path} ===\n{content}")
    return "\n\n".join(results)

def find_type_in_package(package_name: str, search_term: str = None) -> str:
    try:
        # Find the package in the NuGet cache
        result = subprocess.run(
            f'find ~/.nuget/packages/{package_name.lower()} -name "*.dll" | head -5',
            shell=True, capture_output=True, text=True
        )
        dlls = result.stdout.strip().splitlines()
        if not dlls:
            return f"Package '{package_name}' not found in NuGet cache. Run dotnet restore first."
        
        dll = dlls[0]
        
        # Use dotnet-script or reflection to list public methods
        script = f"""
            using System.Reflection;
            var asm = Assembly.LoadFrom("{dll}");
            var methods = asm.GetTypes()
                .Where(t => t.IsPublic)
                .SelectMany(t => t.GetMethods(BindingFlags.Public | BindingFlags.Static))
                .Where(m => m.IsDefined(typeof(System.Runtime.CompilerServices.ExtensionAttribute), false))
                .Select(m => $"{{m.DeclaringType?.Name}}.{{m.Name}}({{string.Join(\", \", m.GetParameters().Select(p => p.ParameterType.Name + \" \" + p.Name))}})")
                {"".join([f'.Where(s => s.Contains("{search_term}", StringComparison.OrdinalIgnoreCase))' if search_term else ''])}
                .Distinct()
                .OrderBy(s => s);
            foreach (var m in methods) Console.WriteLine(m);
            """
        # Write and run a temp csx script
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix='.csx', mode='w', delete=False) as f:
            f.write(script)
            tmp = f.name
        
        result = subprocess.run(
            f'dotnet script {tmp}',
            shell=True, capture_output=True, text=True, timeout=30
        )
        os.unlink(tmp)
        
        output = result.stdout.strip() or result.stderr.strip()
        return output[:3000] if output else "No extension methods found."
    except Exception as e:
        return f"ERROR: {e}"
