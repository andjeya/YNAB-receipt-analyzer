from __future__ import annotations

from pathlib import Path


def test_no_direct_google_genai_imports_outside_gateway_provider():
    repo_root = Path(__file__).resolve().parents[2]
    backend_files = list((repo_root / "backend").rglob("*.py"))
    shared_files = list((repo_root / "shared").rglob("*.py"))

    allowed = {
        (repo_root / "shared" / "receipt_shared" / "ai" / "providers" / "gemini.py").resolve(),
    }

    bad_files: list[str] = []
    patterns = (
        "from google import genai",
        "from google.genai",
        "google.genai",
    )

    for file_path in backend_files + shared_files:
        if "tests" in file_path.parts:
            continue
        resolved = file_path.resolve()
        if resolved in allowed:
            continue
        text = file_path.read_text(encoding="utf-8")
        if any(pattern in text for pattern in patterns):
            bad_files.append(str(file_path.relative_to(repo_root)))

    assert bad_files == [], f"Direct provider SDK usage bypasses AI gateway: {bad_files}"
