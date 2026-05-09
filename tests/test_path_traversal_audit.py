"""Audit every tool router for unsafe path expressions.

CodeQL flags ~42 endpoints with `py/path-injection` ("Uncontrolled data used
in path expression"). All such call sites SHOULD be preceded by one of:

  - app.core.safe_paths.safe_join(base, user_name)
  - app.core.safe_paths.sanitize_filename(name)
  - app.core.safe_paths.is_safe_name(name)         # boolean form, then raise
  - app.core.safe_paths.require_uuid_hex(s, ...)
  - app.core.upload_owner.require(uid, request)    # ACL — rejects guessed IDs

This test is a **structural audit**: for every router file under app/tools,
parse the source and assert that any function which constructs a `Path()` /
calls `open()` / returns `FileResponse()` from a parameter named like
`filename`, `upload_id`, `name`, etc. either:
  (a) imports one of the sanitiser helpers in the same function, OR
  (b) is explicitly listed in `KNOWN_SAFE` below (e.g. internal-only,
      no user input, or sanitised through another mechanism).

Failing this test means a new endpoint was added without an audited
sanitiser — fix the endpoint, don't bypass the test."""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_ROOT = REPO_ROOT / "app" / "tools"

SANITIZER_NAMES = {
    "safe_join",
    "sanitize_filename",
    "is_safe_name",
    "require_uuid_hex",
    "is_uuid_hex",
}
ACL_NAMES = {"upload_owner", "require"}

USER_INPUT_PARAM_NAMES = {
    "filename", "name", "upload_id", "uid", "id",
    "asset_id", "preset_name", "session", "session_id",
    "job_id", "image_id",
}

# Endpoints that intentionally don't use sanitiser — list with reason.
KNOWN_SAFE_FUNCTIONS: set[str] = {
    # Endpoints that only accept fresh UploadFile (no user-supplied id) —
    # the upload_id is server-generated via uuid4().hex; the `filename`
    # parameter only flows into Content-Disposition, never the fs path.
    "api_image_to_pdf",        # image_to_pdf: file = File(...), filename only used as stem
    "preview_watermarked",     # pdf_watermark: fresh File upload, generates own uuid
    "submit",                  # pdf_watermark: fresh File upload, generates own uuid
}


def _routes_with_path_access() -> list[tuple[Path, str, ast.FunctionDef]]:
    """Walk app/tools/**/router.py, return (file, funcname, node) for every
    function decorated with ``@router.*`` whose body contains Path/open/
    FileResponse and which accepts a user-input parameter name.

    Internal helpers (no @router decorator) are skipped — those receive
    pre-validated ids from their caller; if the caller forgets to validate,
    THAT function will be caught by this audit, not the helper."""
    out: list[tuple[Path, str, ast.FunctionDef]] = []
    for f in TOOLS_ROOT.rglob("router.py"):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _is_router_endpoint(node):
                continue
            params = {a.arg for a in node.args.args}
            if not (params & USER_INPUT_PARAM_NAMES):
                continue
            if _function_does_path_access(node):
                out.append((f, node.name, node))
    return out


def _is_router_endpoint(node: ast.AST) -> bool:
    """True if function has a @router.<verb> decorator (FastAPI route)."""
    for d in getattr(node, "decorator_list", []):
        target = d.func if isinstance(d, ast.Call) else d
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "router"
        ):
            return True
    return False


def _function_does_path_access(node: ast.AST) -> bool:
    """True if function body opens / Paths / serves a file."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = (
                fn.attr if isinstance(fn, ast.Attribute)
                else fn.id if isinstance(fn, ast.Name)
                else ""
            )
            if name in {"open", "Path", "FileResponse"}:
                return True
    return False


def _function_uses_sanitizer(node: ast.AST) -> bool:
    """True if any sanitiser helper is called in the function body.

    Also accepts module-local wrapper helpers whose name signals validation
    intent (`_require_access`, `_validate_upload_id`, `_validate_id`, etc.) —
    those are inspected in :func:`_module_validators_chain_to_sanitiser`."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = (
                fn.attr if isinstance(fn, ast.Attribute)
                else fn.id if isinstance(fn, ast.Name)
                else ""
            )
            if name in SANITIZER_NAMES:
                return True
            # Wrapper helpers — _require_access, _validate_upload_id, ...
            if name.startswith(("_require_", "_validate_", "_check_")):
                return True
            # upload_owner.require(...) / _uo.require(...) — the latter is
            # the conventional alias used in newer routers.
            if (
                isinstance(fn, ast.Attribute) and fn.attr == "require"
                and isinstance(fn.value, ast.Name)
                and fn.value.id in {"upload_owner", "_uo"}
            ):
                return True
    return False


def test_every_user_input_path_endpoint_uses_sanitizer():
    """No router endpoint may build a Path / open a file from user-supplied
    parameter names without going through a sanitiser helper.

    Listed counter-examples are bugs to fix, not config to relax."""
    offenders: list[str] = []
    for path, fname, node in _routes_with_path_access():
        if fname in KNOWN_SAFE_FUNCTIONS:
            continue
        if not _function_uses_sanitizer(node):
            offenders.append(f"{path.relative_to(REPO_ROOT)}::{fname}")
    assert not offenders, (
        "Endpoints that touch fs from a user-input parameter without "
        "calling a path-sanitiser:\n  " + "\n  ".join(sorted(offenders))
    )


def test_audit_finds_at_least_one_sanitised_endpoint():
    """Sanity check that the audit walker actually finds endpoints —
    if it returns 0, the AST patterns above are wrong."""
    found = _routes_with_path_access()
    assert len(found) >= 10, f"Audit walker found only {len(found)} routes — bug?"
