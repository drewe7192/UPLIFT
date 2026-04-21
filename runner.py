"""
runner.py — Entry point. Run this from the command line.

Usage:
    python runner.py --project /path/to/your/dotnet/project --from "NET 3.1" --to ".NET 9"

Requirements:
    pip install anthropic
    export ANTHROPIC_API_KEY=your_key_here

Tip:
    Make a git commit BEFORE running so you can diff or roll back.
"""

import argparse
import os
import subprocess
import sys
from agent import run_phased_migration
from dotenv import load_dotenv
load_dotenv()  # loads .env into environment variables

def main():
    parser = argparse.ArgumentParser(
        description="AI agent that migrates .NET projects to newer versions."
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Path to the .NET project directory"
    )
    parser.add_argument(
        "--from-version",
        default=".NET 3.1",
        help='Source .NET version, e.g. ".NET 3.1" (default: .NET 3.1)'
    )
    parser.add_argument(
        "--to-version",
        default=".NET 10",
        help='Target .NET version, e.g. ".NET 9" (default: .NET 9)'
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose tool output"
    )

    args = parser.parse_args()

    # Validate project path
    if not os.path.isdir(args.project):
        print(f"❌ Project path does not exist: {args.project}")
        sys.exit(1)

    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("❌ ANTHROPIC_API_KEY environment variable not set.")
        print("   Export it with: export ANTHROPIC_API_KEY=your_key_here")
        sys.exit(1)

    # Safety reminder
    print("\n⚠️  IMPORTANT: Make sure you have a git commit or backup before running!")
    print("   This agent will modify your files directly.\n")
    confirm = input("   Type 'yes' to continue: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        sys.exit(0)

    # Run the agent
    run_phased_migration(args.project, args.from_version, args.to_version, not args.quiet)

if __name__ == "__main__":
    main()
