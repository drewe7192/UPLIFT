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