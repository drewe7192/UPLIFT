import re

def apply_package_updates(csproj_files: list[str], resolved_versions: dict[str, str]) -> list[str]:
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
        
        for pkg, ver in resolved_versions.items():
            if ver == "REMOVE":
                # Remove self-closing: <PackageReference Include="Pkg" Version="x" />
                content = re.sub(
                    rf'\s*<PackageReference Include="{re.escape(pkg)}"[^/]*/>\n?',
                    "",
                    content,
                    flags=re.IGNORECASE
                )
                # Remove block form: <PackageReference ...>...</PackageReference>
                content = re.sub(
                    rf'\s*<PackageReference Include="{re.escape(pkg)}".*?</PackageReference>\n?',
                    "",
                    content,
                    flags=re.IGNORECASE | re.DOTALL
                )
            else:
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

    COMPATIBILITY_OVERRIDES = {
        "AutoMapper": "12.0.1",
        "AutoMapper.Extensions.Microsoft.DependencyInjection": "12.0.1",
        "FluentValidation": "11.11.0",
        "FluentValidation.AspNetCore": "11.3.1",
        "FluentValidation.DependencyInjectionExtensions": "11.11.0",
        "Microsoft.AspNetCore.Authentication.JwtBearer": "10.0.7",
        # MediatR 12+ merged DI extension in — this package must die
        "MediatR.Extensions.Microsoft.DependencyInjection": "REMOVE",
        # Deprecated after .NET 7, no net8/9/10 release exists
        "Microsoft.AspNetCore.ApiAuthorization.IdentityServer": "6.0.27",
    }

    # Packages known to NOT support net10.0 even in latest stable
    TFM_UNSUPPORTED = {
        "Microsoft.AspNetCore.ApiAuthorization.IdentityServer",
    }

    def get_registration_entry(pkg: str, version: str) -> dict:
        import urllib.request, gzip, json
        url = f"https://api.nuget.org/v3/registration5-gz-semver2/{pkg.lower()}/{version}.json"
        try:
            req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                if r.info().get("Content-Encoding") == "gzip" or raw[:2] == b'\x1f\x8b':
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except Exception:
            return {}

    def supports_tfm(pkg: str, version: str, tfm: str = "net10.0") -> bool:
        if pkg in TFM_UNSUPPORTED:
            return False
        entry = get_registration_entry(pkg, version)
        dep_groups = entry.get("dependencyGroups", [])
        if not dep_groups:
            return True  # no TFM restriction listed, assume compatible
        supported = [
            g.get("targetFramework", "").lower()
            for g in dep_groups
        ]
        # net10.0 should match "net10.0", ".netcoreapp", "net5.0+" era wildcards, or empty
        return any(
            tfm in t or t in ("", "any", ".netcoreapp3.1")
            or t.startswith("net1") or t.startswith("net9") or t.startswith("net8")
            for t in supported
        ) or not any(t for t in supported)  # all empty = universal

    def resolve_one(pkg: str) -> str | None:
        import urllib.request, json
        url = f"https://api.nuget.org/v3-flatcontainer/{pkg.lower()}/index.json"
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                data = json.loads(r.read())
        except Exception:
            return None

        stable = [
            v for v in data.get("versions", [])
            if not any(x in v for x in ["-alpha", "-beta", "-preview", "-rc"])
        ]
        for v in reversed(stable):
            if supports_tfm(pkg, v):
                return v
        return stable[-1] if stable else None
    versions = {}
    for pkg in sorted(packages):
        override = COMPATIBILITY_OVERRIDES.get(pkg)

        if override == "REMOVE":
            versions[pkg] = "REMOVE"
            print(f"   🗑️  {pkg}: REMOVE (obsolete package)")
            continue

        if override:
            versions[pkg] = override
            print(f"   📌 {pkg}: {override} (pinned for compatibility)")
            continue

        v = resolve_one(pkg)
        if v:
            versions[pkg] = v
            print(f"   ✅ {pkg}: {v}")
        else:
            print(f"   ❌ {pkg}: failed to resolve, skipping")

    return versions