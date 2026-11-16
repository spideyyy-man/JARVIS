"""File exploration (read level) and file management (confirm level)."""
from __future__ import annotations

import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from ..security import Level, SecurityError
from .registry import Tool, ToolContext, ToolRegistry

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".cache", ".mypy_cache", ".pytest_cache", "dist", "build", ".next", "target", ".tox"}
TEXT_LIMIT = 200_000

CATEGORIES = {
    "Images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".heic", ".tiff"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".epub", ".ppt", ".pptx"},
    "Spreadsheets": {".xls", ".xlsx", ".csv", ".tsv", ".ods"},
    "Archives": {".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".xz", ".tgz"},
    "Audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"},
    "Video": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
    "Code": {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".sh", ".json", ".yaml", ".yml", ".toml", ".html", ".css", ".sql", ".ipynb"},
    "Installers": {".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".appimage", ".iso"},
}


def _entry(p: Path) -> dict:
    st = p.lstat()
    return {"name": p.name, "path": str(p), "is_dir": p.is_dir(), "size": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}


def walk(root: Path, guard, include_hidden: bool = False, max_files: int = 60_000, deadline: float | None = None):
    count = 0
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and (include_hidden or not d.startswith("."))]
        for name in files:
            if not include_hidden and name.startswith("."):
                continue
            path = Path(dirpath) / name
            if guard.is_sensitive(path):
                continue
            yield path
            count += 1
            if count >= max_files or (deadline and time.time() > deadline):
                return


def list_dir(ctx: ToolContext, path: str = "", show_hidden: bool = False) -> dict:
    root = ctx.app.guard.resolve(path or str(ctx.app.settings.allowed_roots[0]))
    if not root.is_dir():
        raise FileNotFoundError(f"Not a directory: {root}")
    entries = [_entry(p) for p in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
               if (show_hidden or not p.name.startswith(".")) and not ctx.app.guard.is_sensitive(p)]
    return {"path": str(root), "count": len(entries), "entries": entries[:300]}


def read_file(ctx: ToolContext, path: str, offset: int = 0, max_chars: int = 20_000) -> dict:
    p = ctx.app.guard.resolve(path)
    if not p.is_file():
        raise FileNotFoundError(f"No such file: {p}")
    if p.suffix.lower() in (".pdf", ".docx"):
        from ..knowledge import extract_text

        text = "\n\n".join(t for _loc, t in extract_text(p))
    else:
        raw = p.read_bytes()[: TEXT_LIMIT * 4]
        if b"\x00" in raw[:4096]:
            return {"path": str(p), "binary": True, "size": p.stat().st_size}
        text = raw.decode("utf-8", errors="replace")
    chunk = text[offset: offset + min(max_chars, TEXT_LIMIT)]
    return {"path": str(p), "total_chars": len(text), "offset": offset, "content": chunk, "truncated": offset + len(chunk) < len(text)}


def file_info(ctx: ToolContext, path: str) -> dict:
    p = ctx.app.guard.resolve(path)
    info = _entry(p)
    if p.is_dir():
        children = list(p.iterdir())
        info.update(children=len(children), subdirs=sum(c.is_dir() for c in children))
    return info


def search_files(ctx: ToolContext, root: str = "", keywords: list[str] | None = None, extensions: list[str] | None = None,
                 contains_text: str = "", modified_within_days: int = 0, min_size_mb: float = 0, limit: int = 25) -> dict:
    """Ranked search. Keywords match (any, scored) against the file name and path."""
    base = ctx.app.guard.resolve(root or str(ctx.app.settings.allowed_roots[0]))
    kws = [k.lower() for k in (keywords or []) if k.strip()]
    exts = {e.lower() if e.startswith(".") else "." + e.lower() for e in (extensions or [])}
    cutoff = time.time() - modified_within_days * 86400 if modified_within_days else 0
    deadline = time.time() + 20
    hits, scanned = [], 0
    for p in walk(base, ctx.app.guard, deadline=deadline):
        scanned += 1
        if exts and p.suffix.lower() not in exts:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if cutoff and st.st_mtime < cutoff:
            continue
        if min_size_mb and st.st_size < min_size_mb * 1e6:
            continue
        name, rel = p.name.lower(), str(p.relative_to(base)).lower()
        score = sum(3 if k in name else 1 if k in rel else 0 for k in kws)
        if kws and score == 0 and not contains_text:
            continue
        match_line = None
        if contains_text:
            if st.st_size > 2_000_000:
                continue
            try:
                for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                    if contains_text.lower() in line.lower():
                        match_line = {"line": i, "text": line.strip()[:200]}
                        score += 2
                        break
            except OSError:
                continue
            if match_line is None:
                continue
        hits.append((score, st.st_mtime, {"path": str(p), "size": st.st_size, "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"), **({"match": match_line} if match_line else {})}))
    hits.sort(key=lambda h: (h[0], h[1]), reverse=True)
    return {"root": str(base), "scanned": scanned, "matched": len(hits), "results": [h[2] for h in hits[:limit]], "timed_out": time.time() > deadline}


def recent_files(ctx: ToolContext, root: str = "", days: int = 1, limit: int = 20) -> dict:
    return search_files(ctx, root=root, modified_within_days=max(days, 1), limit=limit)


# ---------------------------------------------------------------- modifications
def write_file(ctx: ToolContext, path: str, content: str, overwrite: bool = False) -> dict:
    p = ctx.app.guard.resolve(path, write=True)
    if p.exists() and not overwrite:
        raise FileExistsError(f"{p} exists; pass overwrite=true to replace it")
    backup = None
    if p.exists():
        backup = _to_trash(ctx, p, copy=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return {"written": str(p), "bytes": len(content.encode()), "backup_of_previous": backup}


def replace_in_file(ctx: ToolContext, path: str, old: str, new: str) -> dict:
    p = ctx.app.guard.resolve(path, write=True)
    text = p.read_text()
    if text.count(old) != 1:
        raise ValueError(f"`old` must occur exactly once in {p.name}; found {text.count(old)}")
    backup = _to_trash(ctx, p, copy=True)
    p.write_text(text.replace(old, new))
    return {"edited": str(p), "backup_of_previous": backup}


def create_directory(ctx: ToolContext, path: str) -> dict:
    p = ctx.app.guard.resolve(path, write=True)
    p.mkdir(parents=True, exist_ok=True)
    return {"created": str(p)}


def move_path(ctx: ToolContext, source: str, destination: str) -> dict:
    src, dst = ctx.app.guard.resolve(source, write=True), ctx.app.guard.resolve(destination, write=True)
    if not src.exists():
        raise FileNotFoundError(str(src))
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        raise FileExistsError(f"Destination exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"moved": str(src), "to": str(dst)}


def copy_path(ctx: ToolContext, source: str, destination: str) -> dict:
    src, dst = ctx.app.guard.resolve(source), ctx.app.guard.resolve(destination, write=True)
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        raise FileExistsError(f"Destination exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    (shutil.copytree if src.is_dir() else shutil.copy2)(str(src), str(dst))
    return {"copied": str(src), "to": str(dst)}


def _to_trash(ctx: ToolContext, p: Path, copy: bool = False) -> str:
    dest = ctx.app.settings.trash_dir / f"{int(time.time() * 1000)}_{p.name}"
    if copy:
        (shutil.copytree if p.is_dir() else shutil.copy2)(str(p), str(dest))
    else:
        shutil.move(str(p), str(dest))
    index = ctx.app.db.get_setting("trash_index", {})
    index[dest.name] = {"original": str(p), "at": time.time(), "kind": "backup" if copy else "deleted"}
    ctx.app.db.set_setting("trash_index", dict(sorted(index.items())[-500:]))
    return str(dest)


def list_trash(ctx: ToolContext) -> dict:
    index = ctx.app.db.get_setting("trash_index", {})
    items = [{"name": n, **meta} for n, meta in index.items() if (ctx.app.settings.trash_dir / n).exists()]
    return {"items": sorted(items, key=lambda i: i["at"], reverse=True)[:100], "trash_folder": str(ctx.app.settings.trash_dir)}


def _trash_entry(ctx: ToolContext, name: str) -> tuple[str, dict]:
    items = list_trash(ctx)["items"]
    if name:
        hit = next((i for i in items if i["name"] == name or i["name"].endswith("_" + name) or Path(i["original"]).name == name), None)
    else:  # default: the most recent real deletion
        hit = next((i for i in items if i["kind"] == "deleted"), None)
    if not hit:
        raise FileNotFoundError("Nothing matching that is in JARVIS's trash")
    return hit["name"], hit


def restore_from_trash(ctx: ToolContext, name: str = "") -> dict:
    """Undo: put a deleted file back, or roll a file back to the version saved before an edit."""
    trash_name, meta = _trash_entry(ctx, name)
    original = ctx.app.guard.resolve(meta["original"], write=True)
    replaced = None
    if original.exists():
        if meta["kind"] != "backup":
            raise FileExistsError(f"{original} exists again; move it away first")
        replaced = _to_trash(ctx, original)  # keep the current version recoverable too
    original.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(ctx.app.settings.trash_dir / trash_name), str(original))
    index = ctx.app.db.get_setting("trash_index", {})
    index.pop(trash_name, None)
    ctx.app.db.set_setting("trash_index", index)
    return {"restored": str(original), "was": meta["kind"], "current_version_saved_as": replaced}


def delete_path(ctx: ToolContext, path: str) -> dict:
    """Never unlinks: moves into JARVIS's trash so a mistaken approval is recoverable."""
    p = ctx.app.guard.resolve(path, write=True)
    if not p.exists():
        raise FileNotFoundError(str(p))
    return {"deleted": str(p), "recoverable_from": _to_trash(ctx, p)}


def _organize_plan(ctx: ToolContext, path: str) -> tuple[Path, list[dict]]:
    root = ctx.app.guard.resolve(path, write=False)
    if not root.is_dir():
        raise FileNotFoundError(f"Not a directory: {root}")
    moves = []
    for p in sorted(root.iterdir()):
        if p.is_dir() or p.name.startswith(".") or ctx.app.guard.is_sensitive(p):
            continue
        category = next((c for c, exts in CATEGORIES.items() if p.suffix.lower() in exts), "Other")
        moves.append({"file": p.name, "to": f"{category}/{p.name}"})
    return root, moves


def organize_folder_plan(ctx: ToolContext, path: str) -> dict:
    root, moves = _organize_plan(ctx, path)
    by_cat: dict[str, int] = {}
    for m in moves:
        by_cat[m["to"].split("/")[0]] = by_cat.get(m["to"].split("/")[0], 0) + 1
    return {"path": str(root), "files": len(moves), "by_category": by_cat, "moves": moves[:200],
            "note": "This is a dry run. Nothing was moved. Call organize_folder to apply it (requires approval)."}


def organize_folder(ctx: ToolContext, path: str) -> dict:
    root, moves = _organize_plan(ctx, path)
    ctx.app.guard.resolve(root / "x", write=True)
    done, skipped = [], []
    for m in moves:
        dst = root / m["to"]
        if dst.exists():
            skipped.append(m["file"])
            continue
        dst.parent.mkdir(exist_ok=True)
        shutil.move(str(root / m["file"]), str(dst))
        done.append(m)
    return {"path": str(root), "moved": len(done), "skipped_existing": skipped, "moves": done[:200]}


def _organize_preview(app):
    def preview(a: dict) -> str:
        ctx = ToolContext(app)
        root, moves = _organize_plan(ctx, a["path"])
        if not moves:
            raise SecurityError(f"Nothing to organize in {root}")
        sample = ", ".join(f"{m['file']} -> {m['to'].split('/')[0]}/" for m in moves[:6])
        return f"Organize {root}: move {len(moves)} files into category subfolders ({sample}{' ...' if len(moves) > 6 else ''})"
    return preview


def register(reg: ToolRegistry, app) -> None:
    S = {"type": "string"}
    g = app.guard
    reg.register(Tool("list_dir", "List a directory's contents.", {"path": S, "show_hidden": {"type": "boolean"}}, list_dir))
    reg.register(Tool("read_file", "Read a text, PDF or DOCX file (paged by offset/max_chars).", {"path": S, "offset": {"type": "integer"}, "max_chars": {"type": "integer"}}, read_file, required=["path"]))
    reg.register(Tool("file_info", "Size, modified time and type of a file or folder.", {"path": S}, file_info, required=["path"]))
    reg.register(Tool("search_files", "Find files. Translate the user's natural-language description into filters: keywords matched against file names/paths, extensions, text the file must contain, recency, size.", {
        "root": {"type": "string", "description": "Folder to search (default: home)"},
        "keywords": {"type": "array", "items": S, "description": "Words likely to appear in the file name or path"},
        "extensions": {"type": "array", "items": S}, "contains_text": S,
        "modified_within_days": {"type": "integer"}, "min_size_mb": {"type": "number"}, "limit": {"type": "integer"}}, search_files, timeout=40))
    reg.register(Tool("recent_files", "Files modified in the last N days under a folder.", {"root": S, "days": {"type": "integer"}, "limit": {"type": "integer"}}, recent_files, timeout=40))
    reg.register(Tool("organize_folder_plan", "Dry run: show how a folder's loose files would be sorted into category subfolders. Changes nothing.", {"path": S}, organize_folder_plan, required=["path"]))

    reg.register(Tool("list_trash", "Files JARVIS deleted or backed up before editing; these can be restored.", {}, list_trash))

    C = Level.CONFIRM
    reg.register(Tool("restore_from_trash", "Undo a delete, or roll a file back to its version from before an edit. With no name, restores the most recent deletion.",
                      {"name": S}, restore_from_trash, level=C, trust_eligible=True,
                      preview=lambda a: (lambda n, m: f"Restore {m['original']} ({'undo delete' if m['kind'] == 'deleted' else 'roll back to the version before the last edit'})")(*_trash_entry(ToolContext(app), a.get("name", "")))))
    reg.register(Tool("organize_folder", "Sort a folder's loose files into category subfolders (Images, Documents, ...).", {"path": S}, organize_folder, required=["path"], level=C, preview=_organize_preview(app)))
    reg.register(Tool("write_file", "Create a file, or replace one with overwrite=true (the previous version is backed up).", {"path": S, "content": S, "overwrite": {"type": "boolean"}}, write_file, required=["path", "content"], level=C,
                      preview=lambda a: f"{'Overwrite' if a.get('overwrite') else 'Create'} file {g.resolve(a['path'], write=True)} ({len(a['content'])} chars)\n---\n{a['content'][:600]}"))
    reg.register(Tool("replace_in_file", "Edit a file by replacing one exact, unique snippet (previous version is backed up).", {"path": S, "old": S, "new": S}, replace_in_file, required=["path", "old", "new"], level=C,
                      preview=lambda a: f"Edit {g.resolve(a['path'], write=True)}\n- {a['old'][:300]}\n+ {a['new'][:300]}"))
    reg.register(Tool("create_directory", "Create a folder.", {"path": S}, create_directory, required=["path"], level=C, trust_eligible=True,
                      preview=lambda a: f"Create directory {g.resolve(a['path'], write=True)}"))
    reg.register(Tool("move_path", "Move or rename a file/folder.", {"source": S, "destination": S}, move_path, required=["source", "destination"], level=C,
                      preview=lambda a: f"Move {g.resolve(a['source'], write=True)} -> {g.resolve(a['destination'], write=True)}"))
    reg.register(Tool("copy_path", "Copy a file/folder.", {"source": S, "destination": S}, copy_path, required=["source", "destination"], level=C, trust_eligible=True,
                      preview=lambda a: f"Copy {g.resolve(a['source'])} -> {g.resolve(a['destination'], write=True)}"))
    reg.register(Tool("delete_path", "Delete a file/folder (moved to JARVIS trash, recoverable).", {"path": S}, delete_path, required=["path"], level=C,
                      preview=lambda a: f"DELETE {g.resolve(a['path'], write=True)} (moved to trash, recoverable)"))
