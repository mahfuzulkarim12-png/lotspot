"""AC3 — vulnerability posture of the shipped image, triaged by severity.

Counting findings is not triage. The policy these tests encode is:

  1. Nothing that upstream has already fixed may ship (that IS actionable).
  2. The app's own runtime dependency tree must be clean at EVERY severity —
     including MEDIUM/LOW/UNKNOWN, which a count-based check hides.
  3. Every CVE trivy reports, at every severity, must appear in the committed
     triage document. A new finding nobody has looked at turns this red.

(3) is what makes MEDIUM/LOW/UNKNOWN genuinely triaged rather than tallied.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import IMAGE, REPO_ROOT

pytestmark = [pytest.mark.trivy, pytest.mark.docker]

TRIAGE_DOC = REPO_ROOT / "deploy" / "security" / "trivy-triage.md"

# Packages the application actually imports at runtime. A finding in any of
# these is ours to fix; a finding in the Debian base image is not.
RUNTIME_PACKAGES = {
    "fastapi",
    "uvicorn",
    "starlette",
    "pydantic",
    "pydantic-core",
    "anyio",
    "httptools",
    "websockets",
    "watchfiles",
    "uvloop",
    "click",
    "h11",
}

def triaged_ids(text: str) -> set:
    """Vulnerability IDs listed in the doc's markdown tables.

    Reads the first cell of each table row rather than pattern-matching ID
    shapes — trivy emits CVE-*, GHSA-*, DLA-*, and Debian TEMP-* identifiers,
    and a shape-based regex silently drops the ones it does not know.
    """
    ids = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        candidate = cells[0].strip("`* ")
        if candidate and candidate not in {"CVE", "Severity", "---"} and "-" in candidate:
            ids.add(candidate)
    return ids


@pytest.fixture(scope="session")
def trivy_report():
    """Full JSON scan of the image under test.

    Set LOTSPOT_TRIVY_JSON to reuse an existing scan instead of re-running.
    """
    if shutil.which("trivy") is None:
        pytest.skip("trivy CLI not installed — `brew install trivy` and re-run")

    cached = os.environ.get("LOTSPOT_TRIVY_JSON")
    if cached and Path(cached).exists():
        return json.loads(Path(cached).read_text())

    proc = subprocess.run(
        ["trivy", "image", "--scanners", "vuln", "--format", "json", IMAGE],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, f"trivy failed: {proc.stderr[-2000:]}"
    return json.loads(proc.stdout)


def all_vulns(report):
    out = []
    for result in report.get("Results") or []:
        for vuln in result.get("Vulnerabilities") or []:
            out.append({**vuln, "_target": result.get("Target"), "_class": result.get("Class")})
    return out


def test_scan_actually_found_packages(trivy_report):
    """Guard against a vacuous pass: an empty/failed scan must not read as clean."""
    results = trivy_report.get("Results") or []
    assert results, "trivy returned no Results — the scan did not inspect the image"
    targets = {r.get("Class") for r in results}
    assert "os-pkgs" in targets, (
        f"trivy did not scan OS packages (classes seen: {targets}); "
        "a clean report here would be meaningless"
    )


def test_no_vulnerability_with_an_available_fix_ships(trivy_report):
    """Anything upstream has already patched must be patched here.

    This is the enforceable half of the policy — unfixed base-image CVEs are
    outside our control, but a shipped-with-a-fix-available CVE is a choice.
    """
    fixable = [
        v
        for v in all_vulns(trivy_report)
        if v.get("FixedVersion")
    ]
    detail = "\n".join(
        f"  {v['Severity']:<8} {v['PkgName']} {v.get('InstalledVersion')} "
        f"-> {v['FixedVersion']}  {v['VulnerabilityID']}"
        for v in sorted(fixable, key=lambda v: (v["PkgName"], v["VulnerabilityID"]))
    )
    assert not fixable, (
        f"{len(fixable)} vulnerabilities have an upstream fix available and are "
        f"still shipping:\n{detail}"
    )


def test_runtime_dependencies_are_clean_at_every_severity(trivy_report):
    """No finding of ANY severity in a package the app imports.

    Severity-filtered gates let MEDIUM/LOW rot accumulate in the dependency
    tree; this asserts the whole tree, unfiltered.
    """
    hits = [
        v
        for v in all_vulns(trivy_report)
        if v.get("PkgName", "").lower() in RUNTIME_PACKAGES
    ]
    detail = "\n".join(
        f"  {v['Severity']:<8} {v['PkgName']} {v.get('InstalledVersion')} {v['VulnerabilityID']}"
        for v in hits
    )
    assert not hits, (
        f"{len(hits)} vulnerabilities in application runtime dependencies:\n{detail}"
    )


def test_every_finding_appears_in_the_triage_document(trivy_report):
    """MEDIUM/LOW/UNKNOWN included — an untriaged finding fails the build.

    Regenerate with: python deploy/security/generate_triage.py
    """
    assert TRIAGE_DOC.exists(), (
        f"missing triage document {TRIAGE_DOC.relative_to(REPO_ROOT)} — "
        "run python deploy/security/generate_triage.py"
    )
    triaged = triaged_ids(TRIAGE_DOC.read_text())
    found = {v["VulnerabilityID"] for v in all_vulns(trivy_report)}

    missing = sorted(found - triaged)
    assert not missing, (
        f"{len(missing)} findings are not accounted for in "
        f"{TRIAGE_DOC.relative_to(REPO_ROOT)}:\n  " + "\n  ".join(missing[:40])
        + ("\n  ..." if len(missing) > 40 else "")
        + "\nRegenerate with: python deploy/security/generate_triage.py"
    )


def test_triage_document_records_a_disposition_for_every_severity(trivy_report):
    """Each severity band present in the scan must have a written disposition."""
    severities = {v["Severity"] for v in all_vulns(trivy_report)}
    text = TRIAGE_DOC.read_text() if TRIAGE_DOC.exists() else ""
    missing = sorted(s for s in severities if f"## {s}" not in text)
    assert not missing, (
        f"triage document has no section for severity band(s): {missing}. "
        f"Scan reported: {sorted(severities)}"
    )
