import gzip
import json
import re
import urllib.request


def get_target_major(to_version: str) -> str | None:
    match = re.search(r"(\d+)\.\d+", to_version)
    return match.group(1) if match else None


def apply_package_updates(
    csproj_files: list[str],
    resolved_versions: dict[str, str],
    to_version: str = "net10.0",
) -> list[str]:
    changed = []

    for csproj_path in csproj_files:
        with open(csproj_path) as f:
            content = f.read()

        original = content

        # Update TargetFramework dynamically
        content = re.sub(
            r"<TargetFramework>.*?</TargetFramework>",
            f"<TargetFramework>{to_version}</TargetFramework>",
            content,
        )

        for pkg, ver in resolved_versions.items():
            if ver == "REMOVE":
                content = re.sub(
                    rf'\s*<PackageReference Include="{re.escape(pkg)}"[^/]*/>\n?',
                    "",
                    content,
                    flags=re.IGNORECASE,
                )
                content = re.sub(
                    rf'\s*<PackageReference Include="{re.escape(pkg)}".*?</PackageReference>\n?',
                    "",
                    content,
                    flags=re.IGNORECASE | re.DOTALL,
                )
            else:
                content = re.sub(
                    rf'(<PackageReference Include="{re.escape(pkg)}"[^>]*Version=")[^"]*(")',
                    rf"\g<1>{ver}\g<2>",
                    content,
                    flags=re.IGNORECASE,
                )

        if content != original:
            with open(csproj_path, "w") as f:
                f.write(content)
            changed.append(csproj_path)
            print(f"   ✅ Updated: {csproj_path}")

    return changed


def resolve_package_versions(
    csproj_files: list[str],
    to_version: str = "net10.0",
) -> dict[str, str]:
    packages = set()
    target_major = get_target_major(to_version)

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
        "MediatR.Extensions.Microsoft.DependencyInjection": "REMOVE",
        "Microsoft.AspNetCore.ApiAuthorization.IdentityServer": "6.0.27",
        "Swashbuckle.AspNetCore": "6.9.0",
        "Swashbuckle.AspNetCore.Swagger": "6.9.0",
        "Swashbuckle.AspNetCore.SwaggerGen": "6.9.0",
        "Swashbuckle.AspNetCore.SwaggerUI": "6.9.0",
        "Swashbuckle.AspNetCore.Annotations": "6.9.0",
    }

    TFM_UNSUPPORTED = {
        "Microsoft.AspNetCore.ApiAuthorization.IdentityServer",
    }

    # Packages whose major version must match the target .NET SDK major version
    RUNTIME_ALIGNED_PREFIXES = (
        "Microsoft.EntityFrameworkCore",
        "Microsoft.AspNetCore",
        "Microsoft.Extensions",
        "Microsoft.VisualStudio.Web.CodeGeneration",
    )

    def get_registration_entry(pkg: str, version: str) -> dict:
        url = f"https://api.nuget.org/v3/registration5-gz-semver2/{pkg.lower()}/{version}.json"
        try:
            req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                if r.info().get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except Exception:
            return {}

    def supports_tfm(pkg: str, version: str, tfm: str) -> bool:
        if pkg in TFM_UNSUPPORTED:
            return False
        entry = get_registration_entry(pkg, version)
        dep_groups = entry.get("dependencyGroups", [])
        if not dep_groups:
            return True

        supported = [g.get("targetFramework", "").lower() for g in dep_groups]
        if not any(supported):
            return True

        # Only allow TFMs <= target_major (never allow net11.0 when targeting net10.0)
        target_num = int(target_major) if target_major else 10
        allowed_nets = {f"net{i}.0" for i in range(5, target_num + 1)}

        for t in supported:
            if t in ("", "any", ".netcoreapp3.1") or t.startswith("netstandard"):
                return True
            if tfm.lower() in t or t in allowed_nets:
                return True
        return False

    def resolve_one(pkg: str) -> str | None:
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

        # Lock Microsoft/EF/AspNetCore packages to the target .NET major version
        if target_major and pkg.startswith(RUNTIME_ALIGNED_PREFIXES):
            matching = [v for v in stable if v.startswith(f"{target_major}.")]
            if matching:
                stable = matching

        for v in reversed(stable):
            if supports_tfm(pkg, v, to_version):
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
