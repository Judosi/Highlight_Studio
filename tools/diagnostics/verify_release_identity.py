from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT_HINT = Path(__file__).resolve().parents[2]
_IDENTITY = json.loads((_ROOT_HINT / "release_identity.json").read_text(encoding="utf-8"))
EXPECTED_VERSION = str(_IDENTITY["version"])
EXPECTED_APP_VERSION = str(_IDENTITY["app_version"])
EXPECTED_ROOT = f"Highlight_Studio_{EXPECTED_VERSION}"
EXPECTED_DESIGN = str(_IDENTITY["design_id"])
ASSET_MARKER = EXPECTED_VERSION.replace(".", "")


def verify(root: Path) -> list[str]:
    problems: list[str] = []
    root = root.resolve()
    sys.path.insert(0, str(_ROOT_HINT))
    from tools.release.frontend_integrity import verify_frontend
    problems.extend(verify_frontend(root))
    for relative in ('frontend/package.json', 'frontend/package-lock.json',
                     'desktop/electron/package.json', 'desktop/electron/package-lock.json',
                     'desktop/tauri/src-tauri/tauri.conf.json'):
        try:
            data = json.loads((root / relative).read_text(encoding='utf-8'))
            version = data.get('version', data.get('package', {}).get('version'))
            if version != EXPECTED_VERSION:
                problems.append(f'{relative}: wrong version')
            if 'packages' in data and data['packages'].get('', {}).get('version') != EXPECTED_VERSION:
                problems.append(f'{relative}: wrong root package version')
        except (OSError, ValueError, AttributeError) as exc:
            problems.append(f'{relative}: invalid metadata: {exc}')
    for relative in ('README.md', 'docs/ARCHITECTURE.md',
                     f'docs/releases/11.2/BUILD_INFO_V{ASSET_MARKER}_RU.txt',
                     f'docs/releases/11.2/CHANGE_REPORT_V{ASSET_MARKER}_RU.md',
                     f'docs/releases/11.2/TECHNICAL_AUDIT_{EXPECTED_VERSION}_RU.md'):
        if not (root / relative).is_file() or EXPECTED_VERSION not in (root / relative).read_text(encoding='utf-8'):
            problems.append(f'{relative}: missing current release documentation')
    assets = list((root / 'frontend/dist/assets').glob('index-*.js'))
    if len(assets) != 1 or not assets[0].name.startswith(f'index-{ASSET_MARKER}-'):
        problems.append('frontend must contain exactly one current entry bundle')
    # The package identity is verified from its signed/release files and built assets.
    # Do not reject a correct package only because Windows Explorer renamed the
    # extraction folder (for example Highlight_Studio_11.2.7 (1)).
    if not root.name.lower().startswith(EXPECTED_ROOT.lower()):
        # Keep this as a warning-free compatibility path: portable source builds
        # may live in any user-selected folder. Content checks below are authoritative.
        pass

    required = [
        root / "frontend" / "dist" / "index.html",
        root / "frontend" / "dist" / "release.json",
        root / "frontend" / "package.json",
        root / "backend" / "src" / "highlight_studio" / "core" / "settings.py",
    ]
    for path in required:
        if not path.is_file():
            problems.append(f"missing required file: {path.relative_to(root)}")
    if problems:
        return problems

    index_text = required[0].read_text(encoding="utf-8")
    if f"<title>Highlight Studio v{EXPECTED_VERSION}</title>" not in index_text:
        problems.append("frontend/dist/index.html has the wrong title/version")
    js_match = re.search(r'src="(/assets/[^"]+\.js)"', index_text)
    css_match = re.search(r'href="(/assets/[^"]+\.css)"', index_text)
    if not js_match or not css_match:
        problems.append("frontend/dist/index.html does not reference hashed JS/CSS assets")
        return problems
    # Release-specific asset names are deliberate: backend serves assets as
    # immutable for one year, so reusing an old bundle filename can make the
    # browser show a previous UI even when the new backend is running.
    if ASSET_MARKER not in js_match.group(1) or ASSET_MARKER not in css_match.group(1):
        problems.append("frontend assets are not release-specific; stale browser cache could load an older UI")

    release = json.loads(required[1].read_text(encoding="utf-8"))
    if release.get("version") != EXPECTED_VERSION:
        problems.append("frontend/dist/release.json has the wrong semantic version")
    if release.get("app_version") != EXPECTED_APP_VERSION:
        problems.append("frontend/dist/release.json has the wrong app version")
    if release.get("design_id") != EXPECTED_DESIGN:
        problems.append("frontend/dist/release.json has the wrong design identity")

    js_path = root / "frontend" / "dist" / js_match.group(1).lstrip("/")
    css_path = root / "frontend" / "dist" / css_match.group(1).lstrip("/")
    for path in (js_path, css_path):
        if not path.is_file():
            problems.append(f"referenced frontend asset is missing: {path.relative_to(root)}")
    if problems:
        return problems

    js_text = js_path.read_text(encoding="utf-8")
    css_text = css_path.read_text(encoding="utf-8")
    override_css = root / "frontend" / "dist" / "studio-final-101513.css"
    if not override_css.is_file():
        problems.append("frontend/dist/studio-final-101513.css is missing")
    elif "/studio-final-101513.css" not in index_text:
        problems.append("frontend/dist/index.html does not load the audited final stylesheet")
    for legacy_name in ("studio-v4.css", "studio-v5.css", "studio-v6.css", "studio-v7.css"):
        if legacy_name in index_text:
            problems.append(f"frontend/dist/index.html still loads legacy patch layer: {legacy_name}")
    for marker in ("studioSidebar", "studioCommandbar", "uReviewStudioGrid", "sidebarMobileClose", "data-sidebar-state", f"Версия {EXPECTED_VERSION}"):
        if marker not in js_text:
            problems.append(f"built JS is missing new-interface marker: {marker}")
    final_css_text = override_css.read_text(encoding="utf-8") if override_css.is_file() else ""
    for marker in (".studioSidebar", ".studioCommandbar", ".uReviewStudioGrid", ".studioApp.studioAppV2", ".sidebarMobileClose"):
        if marker not in css_text and marker not in final_css_text:
            problems.append(f"frontend styles are missing interface marker: {marker}")

    package = json.loads(required[2].read_text(encoding="utf-8"))
    if package.get("version") != EXPECTED_VERSION:
        problems.append("frontend/package.json has the wrong version")
    settings = required[3].read_text(encoding="utf-8")
    if "release_identity.json" not in settings or "APP_VERSION" not in settings:
        problems.append("backend settings does not use canonical release identity")
    return problems


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    problems = verify(root)
    if problems:
        print("RELEASE IDENTITY FAILED:", file=sys.stderr)
        for problem in problems:
            print(f" - {problem}", file=sys.stderr)
        return 2
    print(f"Release identity OK: {EXPECTED_ROOT} / {EXPECTED_APP_VERSION} / {EXPECTED_DESIGN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
