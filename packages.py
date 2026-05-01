import re

def apply_package_updates(csproj_files: list[str], resolved_versions: dict[str, str]) -> list[str]:
    """Apply resolved versions directly in Python. No agent needed for this."""
    changed = []
    
    for csproj_path in csproj_files:
        with open(csproj_path) as f:
            content = f.read()
        
        original = content
        
        # Update TargetFramework
        content = re.sub(
            r'<TargetFramework>.*?</TargetFramework>',
            '<TargetFramework>net10.0</TargetFramework>',
            content
        )
        
        # Update each package version
        for pkg, ver in resolved_versions.items():
            content = re.sub(
                rf'(<PackageReference Include="{re.escape(pkg)}"[^>]*Version=")[^"]*(")',
                rf'\g<1>{ver}\g<2>',
                content,
                flags=re.IGNORECASE
            )
        
        if content != original:
            with open(csproj_path, 'w') as f:
                f.write(content)
            changed.append(csproj_path)
            print(f"   ✅ Updated: {csproj_path}")
    
    return changed


# resolve compatible versions together, not independently
def resolve_package_versions(csproj_files: list[str]) -> dict[str, str]:
    import re, json, subprocess
    packages = set()

    for csproj in csproj_files:
        with open(csproj) as f:
            content = f.read()
        found = re.findall(r'PackageReference Include="([^"]+)"', content)
        packages.update(found)

    # Known compatible version sets — override NuGet latest when packages are tightly coupled
    COMPATIBILITY_OVERRIDES = {
        # AutoMapper.Extensions 12.0.1 requires AutoMapper exactly 12.0.1
        "AutoMapper": "12.0.1",
        "AutoMapper.Extensions.Microsoft.DependencyInjection": "12.0.1",

        # FluentValidation — AspNetCore 11.3.1 requires >= 11.11.0
        "FluentValidation": "11.11.0",
        "FluentValidation.AspNetCore": "11.3.1",
        "FluentValidation.DependencyInjectionExtensions": "11.11.0",
        "Microsoft.AspNetCore.Authentication.JwtBearer": "10.0.7",
    }

    versions = {}
    for pkg in sorted(packages):
        # Check overrides first
        if pkg in COMPATIBILITY_OVERRIDES:
            versions[pkg] = COMPATIBILITY_OVERRIDES[pkg]
            print(f"   📌 {pkg}: {COMPATIBILITY_OVERRIDES[pkg]} (pinned for compatibility)")
            continue

        # Query NuGet flat container API for everything else
        result = subprocess.run(
            f'curl -s "https://api.nuget.org/v3-flatcontainer/{pkg.lower()}/index.json"',
            shell=True, capture_output=True, text=True, timeout=10
        )
        try:
            data = json.loads(result.stdout)
            all_versions = data.get("versions", [])
            stable = [v for v in all_versions if not any(
                x in v for x in ["-alpha", "-beta", "-preview", "-rc"]
            )]
            if stable:
                versions[pkg] = stable[-1]
                print(f"   ✅ {pkg}: {stable[-1]}")
            else:
                print(f"   ⚠️  {pkg}: no stable version found, skipping")
        except Exception:
            print(f"   ❌ {pkg}: failed to resolve, skipping")

    return versions