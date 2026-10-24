"""Build and verify the JetBrains theme plugins on Windows, macOS or Linux.

    python tools/build.py                      the bundle and every standalone plugin
    python tools/build.py --plugin Abyss       one standalone plugin
    python tools/build.py --overrides personal/overrides.json --plugin All

No Gradle: a theme plugin has no code, so each jar is a descriptor, an icon, theme JSON and scheme XML.

1. Reads the Rider build from product-info.json; since-build is pinned to 262 (the tested 2026.2 line).
2. Extracts JetBrains' base themes, schemes and UI key metadata from the installed Rider into a scratch
   folder. They are build inputs (Apache-2.0) and are not copied into the repository.
3. Checks the language declarations of the four installed IDEs, then generates every theme and scheme from
   the maps in src/; the generator fails on an unknown key, a weak contrast or a contradicted sample.
4. Packs dist/<plugin>-<version>.jar with fixed entry dates and order, so the same inputs give the same jar.
5. Verifies the packed jar, and for the bundle every standalone input, then removes superseded releases.

Requires Python 3.10+ with Pillow and a JDK (javap) on PATH or under JAVA_HOME. Visual Studio is optional and
used only with --refresh-vs or --vs-home (Windows).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from ide_home import PRODUCTS, default_home, parse_homes, product_info
from report_paths import family_reports

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "qa/tools/release_gate.py"
SINCE = "262"
JAR_DATE = (2026, 1, 1, 0, 0, 0)
MANIFEST = b"Manifest-Version: 1.0\r\nCreated-By: FlexThemes build\r\n\r\n"
BLUE_FAMILIES = {"LavenderMist", "BlueMist", "PeriwinkleIslands", "PeriwinkleNight", "Abyss"}
# JetBrains base resources: (scratch folder, jar inside the Rider home, which entries to extract).
BASE_RESOURCES = (
    ("theme-pack", "plugins/rider-theme-pack/lib/rider-theme-pack.jar",
     lambda name: ("/" not in name and name.endswith(".theme.json")) or name.startswith("colorSchemes/")),
    ("platform", "lib/intellij.platform.ide.impl.jar",
     lambda name: name.startswith("themes/") or name == "DefaultColorSchemesManager.xml"),
    ("rider", "lib/intellij.rider.jar", lambda name: name.startswith("colorSchemes/")),
)


class BuildError(Exception):
    pass


def catalogue() -> dict:
    return json.loads((ROOT / "plugins.json").read_text(encoding="utf-8"))


def run(what: str, *command, capture: bool = False) -> str:
    result = subprocess.run([str(part) for part in command], check=False, text=True, encoding="utf-8",
                            stdout=subprocess.PIPE if capture else None)
    if result.returncode != 0:
        raise BuildError(f"{what} failed with exit code {result.returncode}")
    return result.stdout or ""


def tool(script: str, *arguments) -> list:
    return [sys.executable, ROOT / "tools" / script, *arguments]


def warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr, flush=True)


def inside(path: Path, parent: Path) -> Path:
    """Refuse a destructive target outside its expected parent folder."""
    resolved, parent = path.resolve(), parent.resolve()
    if resolved == parent or parent not in resolved.parents:
        raise BuildError(f"{path} escapes {parent}")
    return resolved


def jdk_tool(name: str) -> str:
    found = shutil.which(name)
    if not found and os.environ.get("JAVA_HOME"):
        for candidate in (Path(os.environ["JAVA_HOME"]) / "bin" / f"{name}{suffix}" for suffix in ("", ".exe")):
            if candidate.is_file():
                found = str(candidate)
                break
    if not found:
        raise BuildError(f"{name} was not found on PATH or under JAVA_HOME (any JDK 21 or later provides it).")
    return found


def html_encode(text: str) -> str:
    """HTML-encode change notes the way .NET's WebUtility.HtmlEncode does, which the first builds used."""
    out = []
    for char in text:
        code = ord(char)
        if char in "<>\"&'":
            out.append({"<": "&lt;", ">": "&gt;", '"': "&quot;", "&": "&amp;", "'": "&#39;"}[char])
        elif 160 <= code <= 255 or code > 0xFFFF:
            out.append(f"&#{code};")
        else:
            out.append(char)
    return "".join(out)


def change_notes(changelog: Path) -> str:
    """The newest section of a CHANGELOG.md as an HTML list."""
    text = changelog.read_text(encoding="utf-8").replace("\r\n", "\n")
    sections = re.split(r"(?m)^## ", text)
    if len(sections) < 2:
        raise BuildError(f"{changelog} has no '## <version>' section")
    items = [
        html_encode(re.sub(r"^\s*- ", "", line).strip())
        for line in sections[1].split("\n")[1:]
        if re.match(r"^\s*- ", line)
    ]
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def write_jar(resources: Path, jar: Path) -> None:
    """Pack a folder as a jar with fixed dates, order and attributes, so equal inputs give equal bytes."""
    if (resources / "META-INF/MANIFEST.MF").exists():
        raise BuildError(f"{resources} must not contain its own MANIFEST.MF")
    files = sorted(p.relative_to(resources).as_posix() for p in resources.rglob("*") if p.is_file())
    folders = sorted({"/".join(name.split("/")[:depth]) + "/" for name in files
                      for depth in range(1, name.count("/") + 1)})
    entries = ["META-INF/", "META-INF/MANIFEST.MF"] + sorted(
        set(files) | set(folders) - {"META-INF/"}
    )
    jar.parent.mkdir(parents=True, exist_ok=True)
    partial = jar.with_name(jar.name + ".partial")
    with zipfile.ZipFile(partial, "w") as archive:
        for name in entries:
            info = zipfile.ZipInfo(name, date_time=JAR_DATE)
            info.create_system = 0
            if name.endswith("/"):
                archive.writestr(info, b"")
                continue
            info.compress_type = zipfile.ZIP_DEFLATED
            data = MANIFEST if name == "META-INF/MANIFEST.MF" else (resources / name).read_bytes()
            archive.writestr(info, data)
    partial.replace(jar)


def extract_base_resources(rider: Path, destination: Path) -> None:
    if (destination / "platform/themes/islands/ManyIslandsLight.theme.json").is_file() and (
        destination / "platform/DefaultColorSchemesManager.xml"
    ).is_file():
        return
    print(f"Extracting JetBrains base resources to {destination}", flush=True)
    staging = Path(tempfile.mkdtemp(prefix=destination.name + "-", dir=destination.parent))
    try:
        for folder, jar, wanted in BASE_RESOURCES:
            with zipfile.ZipFile(rider / jar) as archive:
                for name in archive.namelist():
                    if name.endswith("/") or not wanted(name):
                        continue
                    target = staging / folder / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
        if destination.exists():
            shutil.rmtree(destination)
        staging.replace(destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def vs_data(plugin: str, args) -> Path:
    """Decoded Visual Studio data, or data/none so the committed vs-values snapshots are used."""
    data = ROOT / "data"
    if plugin in BLUE_FAMILIES:
        # The Blue family is inspired by Visual Studio 2019/2022's Blue theme; see docs/Development.md.
        blue = data / "ms-blue-17"
        if (blue / "Blue.json").is_file():
            return blue
        print("No decoded Blue data; using the snapshot.")
        return data / "none"
    if args.refresh_vs or args.vs_home:
        return decode_visual_studio(args.vs_home)
    versions = []
    for folder in data.glob("vs-*") if data.is_dir() else ():
        if folder.is_dir() and re.fullmatch(r"vs-\d+(\.\d+){1,3}", folder.name):
            versions.append((tuple(int(part) for part in folder.name[3:].split(".")), folder))
    if versions:
        return max(versions)[1]
    print("No decoded Visual Studio data; using the snapshot in src/vs-values.")
    return data / "none"


def decode_visual_studio(vs_home: str | None) -> Path:
    """Decode an installed Visual Studio 2026 (Windows only) into data/vs-<version>."""
    if sys.platform != "win32":
        raise BuildError("Decoding Visual Studio needs Windows; other systems build from the committed snapshots.")
    vswhere = Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft Visual Studio/Installer/vswhere.exe"
    if not vswhere.is_file():
        raise BuildError("Decoding Visual Studio needs its installer (vswhere.exe) to identify the version.")
    query = ["-path", vs_home] if vs_home else ["-latest"]
    instances = json.loads(run("vswhere", vswhere, *query, "-format", "json", capture=True) or "[]")
    if not instances:
        raise BuildError(f"No Visual Studio instance found ({' '.join(query)}).")
    display = str(instances[0]["catalog"]["productDisplayVersion"])
    match = re.match(r"\d+(\.\d+){1,3}", display)
    if not match:
        raise BuildError(f"Cannot read the Visual Studio version from '{display}'.")
    if not match.group(0).startswith("18."):
        raise BuildError(f"Visual Studio {match.group(0)} is not Visual Studio 2026 (18.x); refusing to decode it.")
    target = ROOT / "data" / f"vs-{match.group(0)}"
    run("Visual Studio theme decoding", *tool("decode_vs_theme.py", instances[0]["installationPath"], target))
    return target


def home_arguments(homes: dict[str, Path]) -> list[str]:
    return [part for code in PRODUCTS for part in ("--home", f"{code}={homes[code]}")]


def visual_check(artifact: Path, plugin: str, homes: dict[str, Path], args) -> None:
    if args.skip_visual_check or args.overrides:
        warn("Native screenshot comparison SKIPPED; this build has no native visual pass "
             "(explicit skip or personal palette).")
        return
    # The native screenshot release gate is kept outside the published repository.
    if not GATE.is_file():
        warn("Native screenshot comparison SKIPPED; the release gate is not part of this checkout.")
        return
    if sys.platform != "win32":
        warn("Native screenshot comparison SKIPPED; the release gate runs on Windows only.")
        return
    run("Native screenshot release gate", sys.executable, GATE, "--artifact", artifact, "--plugin", plugin,
        "--visual-suite", args.visual_suite, *home_arguments(homes))


def build_plugin(name: str, homes: dict[str, Path], args, skip_visual: bool = False) -> Path:
    layout = catalogue()[name]
    source = ROOT / layout["Source"]
    version = (ROOT / layout["Version"]).read_text(encoding="utf-8").strip()
    info = product_info(homes["RD"])
    print(f"{name}: Rider {info['version']} build {info['buildNumber']}; since-build {SINCE}", flush=True)
    rider_resources = Path(args.scratch) / f"rider-{info['buildNumber']}"
    rider_resources.parent.mkdir(parents=True, exist_ok=True)
    extract_base_resources(homes["RD"], rider_resources)
    data = vs_data(name, args)

    # Every plugin uses the same installed declaration evidence. No IDE process or settings are changed.
    evidence = ROOT / "build/language-evidence.json"
    run("installed language declarations",
        *tool("language_mappings.py", "inventory", ROOT / "src/common/language-mappings.json", evidence,
              *home_arguments(homes), "--javap", jdk_tool("javap")))

    resources = inside(ROOT / layout["Output"], ROOT / "build")
    if resources.exists():
        shutil.rmtree(resources)
    (resources / "META-INF").mkdir(parents=True)
    reports = family_reports(ROOT, layout)
    reports.mkdir(parents=True, exist_ok=True)
    generate = tool("generate.py", ROOT, rider_resources, data, resources, "--source", source,
                    "--docs", reports, "--language-evidence", evidence)
    suffix = ""
    if args.overrides:
        generate += ["--overrides", Path(args.overrides).resolve()]
        suffix = "-personal"
    run("generation", *generate)

    # The newest section of the changelog becomes the plugin's change notes.
    template = (source / "META-INF/plugin.xml").read_text(encoding="utf-8").replace("\r\n", "\n")
    descriptor = (template.replace("@VERSION@", version).replace("@SINCE@", SINCE)
                  .replace("@CHANGES@", change_notes(ROOT / layout["Changelog"])))
    with (resources / "META-INF/plugin.xml").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(descriptor)
    for item in (source / "META-INF/pluginIcon.svg", ROOT / "LICENSE", ROOT / "NOTICE"):
        shutil.copyfile(item, resources / "META-INF" / item.name)

    jar = ROOT / "dist" / f"{layout['Jar']}-{version}{suffix}.jar"
    write_jar(resources, jar)
    check = tool("check_pack.py", jar, source, ROOT, evidence, reports, *home_arguments(homes))
    if args.overrides:
        check += ["--overrides", Path(args.overrides).resolve()]
    run("packaged language mappings and UI clarity", *check)
    if not skip_visual:
        visual_check(jar, name, homes, args)
    with zipfile.ZipFile(jar) as archive:
        print(f"\nBuilt {jar}\n" + "\n".join(archive.namelist()), flush=True)
    return jar


def build_family(homes: dict[str, Path], args) -> None:
    if args.plugin == "Bundle" and args.overrides:
        raise BuildError("Bundle is a standard release; build personal overrides with an individual plugin or All.")
    for name in catalogue():
        # Check the finished bundle once, rather than launching the IDEs for every standalone package too.
        build_plugin(name, homes, args, skip_visual=True)
    if args.overrides:
        return
    run("Family verification index", *tool("report_family.py"))
    spec = json.loads((ROOT / "bundle.json").read_text(encoding="utf-8"))
    resources = inside(ROOT / spec["Output"], ROOT / "build")
    if resources.exists():
        shutil.rmtree(resources)
    run("Bundle staging", *tool("bundle.py", "stage", ROOT))
    version = (ROOT / spec["Version"]).read_text(encoding="utf-8").strip()
    dist = ROOT / "dist"
    jar = dist / f"{spec['Jar']}-{version}.jar"
    write_jar(resources, jar)
    run("Bundle verification", *tool("bundle.py", "check", ROOT))
    visual_check(jar, "Bundle", homes, args)
    # Only known numeric releases older than the configured current versions are candidates; the planner
    # checks the finished bundle and every current release before returning a plan.
    plan = run("Release cleanup preflight", *tool("bundle.py", "obsolete", ROOT), capture=True)
    targets = [inside(dist / line[len("REMOVE:"):], dist) for line in plan.splitlines() if line.startswith("REMOVE:")]
    for target in targets:
        if target.parent != dist.resolve():
            raise BuildError(f"cleanup target escapes dist: {target}")
        target.unlink()
    print(f"Built {jar}; removed {len(targets)} superseded release JARs.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plugin", default="Bundle", choices=["Bundle", "All", *catalogue()],
                        help="Bundle (default) and All build every plugin; a name builds one standalone plugin")
    parser.add_argument("--home", action="append", default=[], metavar="PRODUCT=PATH",
                        help="IDE installation for RD, IU, GO or PY (on macOS the .app); defaults to the usual "
                             "per-user install folders on Windows and /Applications on macOS")
    parser.add_argument("--scratch", default=Path(tempfile.gettempdir()) / "FlexThemes-jetbrains", type=Path,
                        help="where the JetBrains base resources are extracted")
    parser.add_argument("--overrides", help="personal overrides JSON (see docs/overrides.example.json)")
    parser.add_argument("--refresh-vs", action="store_true", help="decode the installed Visual Studio 2026 first")
    parser.add_argument("--vs-home", help="decode this Visual Studio 2026 installation first")
    parser.add_argument("--skip-visual-check", action="store_true", help="skip the native screenshot gate")
    parser.add_argument("--visual-suite", default="Showcase", choices=["Showcase", "LegacyRider"])
    args = parser.parse_args(argv)
    try:
        homes = parse_homes(args.home)
        for code in PRODUCTS:
            if code not in homes:
                homes[code] = default_home(code) or Path()
            product_info(homes[code])
    except (ValueError, FileNotFoundError) as error:
        print(f"BUILD FAILED: {error}. Pass --home RD=..., IU=..., GO=..., PY=... "
              "(the installation folder, or the .app on macOS).", file=sys.stderr)
        return 1
    try:
        if args.plugin in ("Bundle", "All"):
            build_family(homes, args)
        else:
            build_plugin(args.plugin, homes, args)
    except (BuildError, OSError, zipfile.BadZipFile) as error:
        print(f"BUILD FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
