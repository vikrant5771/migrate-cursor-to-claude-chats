#!/usr/bin/env python3
"""
Cursor Chat to Claude Code Session Migrator
-------------------------------------------
Migrates Cursor AI composer chat sessions into Claude Code session format (.jsonl).

Usage:
    python3 migrate_cursor_to_claude.py --list
    python3 migrate_cursor_to_claude.py --workspace .
    python3 migrate_cursor_to_claude.py --all
    python3 migrate_cursor_to_claude.py --session-id <composer-id>
"""

import os
import sys
import json
import sqlite3
import re
import uuid
import datetime
import argparse
from urllib.parse import unquote, urlparse
from typing import Dict, List, Any, Optional, Tuple

def get_cursor_user_dir() -> str:
    """Returns the default Cursor user settings directory based on OS."""
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "Cursor", "User")
    elif sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
        return os.path.join(appdata, "Cursor", "User")
    else:
        return os.path.join(home, ".config", "Cursor", "User")

def open_sqlite_readonly(db_path: str) -> sqlite3.Connection:
    """Opens a SQLite database in read-only mode to prevent locking issues."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database file not found: {db_path}")
    uri_path = f"file:{os.path.abspath(db_path)}?mode=ro"
    return sqlite3.connect(uri_path, uri=True)

def parse_workspace_uri(uri_str: str) -> str:
    """Extracts a clean filesystem path from a workspace URI string."""
    if not uri_str:
        return ""
    if uri_str.startswith("file://"):
        parsed = urlparse(uri_str)
        return unquote(parsed.path)
    return uri_str

def get_workspace_mappings(user_dir: str) -> Dict[str, str]:
    """
    Scans workspaceStorage to map composer IDs to their workspace filesystem paths.
    Supports both legacy composerData blobs and modern Cursor view pane keys.
    Returns: Dict[composer_id -> workspace_path]
    """
    ws_storage = os.path.join(user_dir, "workspaceStorage")
    mapping = {}
    if not os.path.exists(ws_storage):
        return mapping

    uuid_pattern = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)

    for folder_name in os.listdir(ws_storage):
        ws_folder = os.path.join(ws_storage, folder_name)
        if not os.path.isdir(ws_folder):
            continue

        ws_json_path = os.path.join(ws_folder, "workspace.json")
        ws_db_path = os.path.join(ws_folder, "state.vscdb")
        workspace_path = ""

        if os.path.exists(ws_json_path):
            try:
                with open(ws_json_path, "r", encoding="utf-8") as f:
                    wdata = json.load(f)
                    uri = wdata.get("folder") or wdata.get("workspace") or ""
                    workspace_path = parse_workspace_uri(uri)
            except Exception:
                pass

        if workspace_path and os.path.exists(ws_db_path):
            abs_ws = os.path.abspath(workspace_path)
            try:
                conn = open_sqlite_readonly(ws_db_path)
                cur = conn.cursor()
                rows = cur.execute("SELECT key, value FROM ItemTable;").fetchall()
                conn.close()

                for k, v in rows:
                    if k == 'composer.composerData' and v:
                        try:
                            cdata = json.loads(v)
                            composers = cdata.get("allComposers") or cdata.get("composers") or []
                            for item in composers:
                                cid = item.get("composerId") if isinstance(item, dict) else item
                                if cid and cid not in mapping:
                                    mapping[cid] = abs_ws
                        except Exception:
                            pass
                    elif 'composer' in k.lower() or 'aichat' in k.lower():
                        matches = uuid_pattern.findall(k)
                        if isinstance(v, str):
                            matches.extend(uuid_pattern.findall(v))
                        for cid in matches:
                            if cid not in mapping:
                                mapping[cid] = abs_ws
            except Exception:
                pass

    return mapping


def infer_workspace_from_composer(composer_data: Dict[str, Any]) -> str:
    """Tries to extract workspace path from composer context or file references."""
    context = composer_data.get("context", {}) or {}
    
    # Check file selections
    file_selections = context.get("fileSelections") or []
    for fs in file_selections:
        uri_obj = fs.get("uri") or {}
        fs_path = uri_obj.get("fsPath") or uri_obj.get("path") or ""
        if fs_path:
            # Common root detection: find project directory parent
            parts = fs_path.split("/")
            if "src" in parts:
                idx = parts.index("src")
                return "/".join(parts[:idx])
            elif len(parts) > 4:
                return "/".join(parts[:-2])

    # Check recently viewed files
    recent = composer_data.get("recentlyViewedFiles") or []
    for r in recent:
        if isinstance(r, str) and r.startswith("/"):
            parts = r.split("/")
            if "src" in parts:
                idx = parts.index("src")
                return "/".join(parts[:idx])

    return ""

def load_cursor_sessions(user_dir: str) -> List[Dict[str, Any]]:
    """
    Extracts all composer sessions and their ordered bubbles from Cursor's state.vscdb.
    """
    global_db = os.path.join(user_dir, "globalStorage", "state.vscdb")
    if not os.path.exists(global_db):
        print(f"Error: Cursor global database not found at {global_db}", file=sys.stderr)
        return []

    ws_mapping = get_workspace_mappings(user_dir)
    conn = open_sqlite_readonly(global_db)
    cur = conn.cursor()

    rows = cur.execute("SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%';").fetchall()
    sessions = []

    for key, val in rows:
        if not val:
            continue
        if isinstance(val, bytes):
            val = val.decode("utf-8", errors="ignore")
        
        try:
            cdata = json.loads(val)
        except Exception:
            continue

        cid = cdata.get("composerId")
        if not cid:
            cid = key.replace("composerData:", "")

        title = cdata.get("name") or cdata.get("text") or "Untitled Session"
        title = title.strip().replace("\n", " ")
        if len(title) > 60:
            title = title[:57] + "..."

        created_at = cdata.get("createdAt") or cdata.get("lastUpdatedAt") or 0
        workspace = ws_mapping.get(cid) or infer_workspace_from_composer(cdata) or ""

        headers = cdata.get("fullConversationHeadersOnly") or []
        bubbles = []

        for h in headers:
            b_id = h.get("bubbleId") if isinstance(h, dict) else h
            if not b_id:
                continue

            b_row = cur.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ?;",
                (f"bubbleId:{cid}:{b_id}",)
            ).fetchone()

            if not b_row or not b_row[0]:
                continue

            b_val = b_row[0]
            if isinstance(b_val, bytes):
                b_val = b_val.decode("utf-8", errors="ignore")
            
            try:
                b_data = json.loads(b_val)
            except Exception:
                continue

            b_type = b_data.get("type")
            text = b_data.get("text") or b_data.get("richText") or ""
            
            if not text:
                # Check for selections or code blocks if text is empty
                code_blocks = b_data.get("suggestedCodeBlocks") or []
                if code_blocks:
                    block_texts = [cb.get("code", "") for cb in code_blocks if isinstance(cb, dict)]
                    text = "\n".join(filter(None, block_texts))

            if text and text.strip():
                role = "user" if b_type == 1 else "assistant"
                bubbles.append({
                    "bubbleId": b_id,
                    "role": role,
                    "text": text.strip(),
                    "timestamp": b_data.get("timestamp") or created_at
                })

        if bubbles:
            sessions.append({
                "composerId": cid,
                "title": title if title else (bubbles[0]["text"][:40] + "..."),
                "workspace": workspace,
                "createdAt": created_at,
                "bubbles": bubbles
            })

    conn.close()
    sessions.sort(key=lambda s: s["createdAt"], reverse=True)
    return sessions

def resolve_workspace_info(ws_path: str) -> Dict[str, Any]:
    """
    Analyzes a workspace path (directory or .code-workspace file).
    Returns metadata including constituent folder paths and primary project directory.
    """
    ws_path = os.path.abspath(ws_path)
    info = {
        "original_path": ws_path,
        "is_code_workspace": False,
        "base_dir": ws_path if os.path.isdir(ws_path) else os.path.dirname(ws_path),
        "folders": [],
        "primary_dir": ws_path if os.path.isdir(ws_path) else os.path.dirname(ws_path)
    }

    if os.path.isfile(ws_path) and ws_path.endswith(".code-workspace"):
        info["is_code_workspace"] = True
        try:
            with open(ws_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            base_dir = os.path.dirname(ws_path)
            info["base_dir"] = base_dir
            folders = []
            for folder_entry in data.get("folders", []):
                p = folder_entry.get("path", "")
                if p:
                    abs_p = os.path.abspath(os.path.join(base_dir, p))
                    folders.append(abs_p)
            info["folders"] = folders

            # Identify primary directory: look for folder named like the code-workspace basename
            ws_base_name = os.path.splitext(os.path.basename(ws_path))[0]
            matched = [f for f in folders if os.path.basename(f) == ws_base_name]
            if matched:
                info["primary_dir"] = matched[0]
            elif folders:
                info["primary_dir"] = folders[0]
            else:
                info["primary_dir"] = base_dir
        except Exception:
            pass

    return info

def generate_claude_project_slug(workspace_path: str) -> str:
    """
    Converts absolute workspace directory path into Claude Code project slug.
    e.g. /Users/john/project -> Users-john-project and -Users-john-project
    """
    abs_path = os.path.abspath(workspace_path)
    # Claude Code replaces all non-alphanumeric chars with dashes
    slug = re.sub(r'[^a-zA-Z0-9]', '-', abs_path)
    # Collapse multiple dashes
    slug = re.sub(r'-+', '-', slug)
    return slug

def export_to_claude_session(
    session: Dict[str, Any],
    output_base_dir: str,
    target_workspace: Optional[str] = None
) -> Tuple[str, int]:
    """
    Converts a Cursor session dictionary to a Claude Code .jsonl session file.
    Returns: (output_file_path, message_count)
    """
    raw_ws = target_workspace or session.get("workspace") or os.getcwd()
    ws_info = resolve_workspace_info(raw_ws)
    # If the workspace path is a .code-workspace file, default to its primary repo directory
    ws_path = ws_info["primary_dir"] if ws_info["is_code_workspace"] else os.path.abspath(raw_ws)
    
    slug = generate_claude_project_slug(ws_path)
    # Format 1: -Users-name-folder, Format 2: Users-name-folder
    slug_dir_name = slug if slug.startswith("-") else f"-{slug}"
    
    project_dir = os.path.join(output_base_dir, slug_dir_name)
    os.makedirs(project_dir, exist_ok=True)
    
    # Link alias slugs if needed (secondary dir without leading dash or code-workspace parent/file)
    alias_paths = [os.path.join(output_base_dir, slug.lstrip("-"))]
    if ws_info["is_code_workspace"]:
        base_slug = generate_claude_project_slug(ws_info["base_dir"])
        alias_paths.append(os.path.join(output_base_dir, base_slug if base_slug.startswith("-") else f"-{base_slug}"))
        ws_file_slug = generate_claude_project_slug(ws_info["original_path"])
        alias_paths.append(os.path.join(output_base_dir, ws_file_slug if ws_file_slug.startswith("-") else f"-{ws_file_slug}"))

    for alt_dir in alias_paths:
        if alt_dir != project_dir and not os.path.exists(alt_dir):
            try:
                os.symlink(project_dir, alt_dir)
            except Exception:
                pass


    session_id = session["composerId"]
    jsonl_file = os.path.join(project_dir, f"{session_id}.jsonl")

    lines = []
    
    # 1. Custom title entry
    lines.append({
        "type": "custom-title",
        "title": session["title"],
        "sessionId": session_id
    })

    # 2. Convert bubbles
    prev_uuid = None
    created_ts = session.get("createdAt") or 0
    base_dt = datetime.datetime.fromtimestamp(created_ts / 1000.0, tz=datetime.timezone.utc) if created_ts > 0 else datetime.datetime.now(datetime.timezone.utc)

    for idx, b in enumerate(session["bubbles"]):
        msg_uuid = str(uuid.uuid4())
        dt = base_dt + datetime.timedelta(seconds=idx * 2)
        ts_str = dt.strftime("%Y-%m-%d%H:%M:%S.%f")[:-3] + "Z"
        ts_iso = dt.isoformat()

        role = b["role"]
        text_content = b["text"]

        if role == "user":
            record = {
                "type": "user",
                "sessionId": session_id,
                "uuid": msg_uuid,
                "parentUuid": prev_uuid,
                "timestamp": ts_iso,
                "cwd": ws_path,
                "version": "1.0.0",
                "message": {
                    "role": "user",
                    "content": text_content
                }
            }
        else:
            record = {
                "type": "assistant",
                "sessionId": session_id,
                "uuid": msg_uuid,
                "parentUuid": prev_uuid,
                "timestamp": ts_iso,
                "cwd": ws_path,
                "version": "1.0.0",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": text_content
                        }
                    ]
                }
            }

        lines.append(record)
        prev_uuid = msg_uuid

    with open(jsonl_file, "w", encoding="utf-8") as f:
        for item in lines:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    return jsonl_file, len(session["bubbles"])

def main():
    parser = argparse.ArgumentParser(
        description="Migrate Cursor chat sessions to Claude Code sessions."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all discovered Cursor chat sessions without migrating."
    )
    parser.add_argument(
        "--workspace",
        type=str,
        help="Target workspace directory to filter sessions for migration (e.g. '.' or '/path/to/project')."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Migrate all discovered Cursor sessions across all workspaces."
    )
    parser.add_argument(
        "--session-id",
        type=str,
        help="Migrate a single Cursor session by composer ID."
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=os.path.expanduser("~/.claude/projects"),
        help="Base output directory for Claude Code projects (default: ~/.claude/projects)."
    )
    parser.add_argument(
        "--target-dir",
        type=str,
        help="Explicit directory path for Claude Code project destination (useful when workspace is a .code-workspace file)."
    )
    parser.add_argument(
        "--include-unspecified",
        action="store_true",
        help="Include sessions whose workspace could not be determined."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview migration actions without writing files."
    )

    args = parser.parse_args()

    user_dir = get_cursor_user_dir()
    if not os.path.exists(user_dir):
        print(f"Error: Cursor user directory not found at {user_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"🔍 Scanning Cursor sessions from: {user_dir}")
    sessions = load_cursor_sessions(user_dir)
    print(f"Found {len(sessions)} Cursor chat session(s).\n")

    if args.list:
        print(f"{'TITLE':<45} {'MESSAGES':<10} {'WORKSPACE':<40} {'ID'}")
        print("-" * 115)
        for s in sessions:
            ws_display = s['workspace'] if s['workspace'] else "(Unspecified)"
            if len(ws_display) > 38:
                ws_display = "..." + ws_display[-35:]
            print(f"{s['title']:<45} {len(s['bubbles']):<10} {ws_display:<40} {s['composerId']}")
        return

    # Filter sessions based on arguments
    target_sessions = []
    filter_ws = None
    target_export_dir = None

    if args.session_id:
        target_sessions = [s for s in sessions if s["composerId"] == args.session_id]
        if not target_sessions:
            print(f"Error: Session ID {args.session_id} not found.", file=sys.stderr)
            sys.exit(1)
        if args.target_dir:
            target_export_dir = os.path.abspath(args.target_dir)
    elif args.workspace:
        ws_info = resolve_workspace_info(args.workspace)
        filter_ws = ws_info["original_path"]
        target_export_dir = os.path.abspath(args.target_dir) if args.target_dir else ws_info["primary_dir"]

        print(f"Filtering sessions for workspace: {filter_ws}")
        if ws_info["is_code_workspace"]:
            print(f"   Multi-root workspace file detected: {os.path.basename(filter_ws)}")
            if ws_info["folders"]:
                print(f"   Workspace member folders: {len(ws_info['folders'])} folder(s)")
            print(f"   Target Claude Code project directory: {target_export_dir}")

        for s in sessions:
            s_ws = s.get("workspace")
            if not s_ws:
                if args.include_unspecified:
                    target_sessions.append(s)
                continue

            s_ws_abs = os.path.abspath(s_ws)

            # Match directly with workspace path
            if s_ws_abs == os.path.abspath(filter_ws):
                target_sessions.append(s)
            elif ws_info["is_code_workspace"]:
                # Match if s_ws is base directory or one of member folders
                if s_ws_abs == os.path.abspath(ws_info["base_dir"]):
                    target_sessions.append(s)
                elif any(s_ws_abs == f or s_ws_abs.startswith(f + os.sep) for f in ws_info["folders"]):
                    target_sessions.append(s)
            elif os.path.isdir(filter_ws):
                # If filter_ws is a directory, check if s_ws is inside it or if s_ws is a code-workspace in it
                if s_ws_abs == os.path.abspath(filter_ws) or s_ws_abs.startswith(os.path.abspath(filter_ws) + os.sep):
                    target_sessions.append(s)
                elif s_ws_abs.endswith(".code-workspace") and os.path.dirname(s_ws_abs) == os.path.abspath(filter_ws):
                    target_sessions.append(s)
    elif args.all:
        target_sessions = sessions
    else:
        # Default: current directory workspace
        filter_ws = os.getcwd()
        print(f"No option specified. Defaulting to current workspace: {filter_ws}")
        target_export_dir = filter_ws
        for s in sessions:
            s_ws = s.get("workspace")
            if s_ws and (os.path.abspath(s_ws) == filter_ws or os.path.abspath(s_ws).startswith(filter_ws + os.sep)):
                target_sessions.append(s)
            elif not s_ws and args.include_unspecified:
                target_sessions.append(s)

    if not target_sessions:
        print("No matching Cursor sessions found to migrate.")
        sys.exit(0)

    print(f"🚀 Preparing to migrate {len(target_sessions)} session(s) into Claude Code...\n")

    migrated_count = 0
    for idx, s in enumerate(target_sessions, 1):
        ws = target_export_dir or filter_ws or s.get("workspace") or os.getcwd()
        slug = generate_claude_project_slug(ws)
        slug_dir = slug if slug.startswith("-") else f"-{slug}"
        expected_path = os.path.join(args.output_dir, slug_dir, f"{s['composerId']}.jsonl")

        print(f"[{idx}/{len(target_sessions)}] {s['title']}")
        print(f"   ├─ Composer ID: {s['composerId']}")
        print(f"   ├─ Messages:    {len(s['bubbles'])}")
        print(f"   ├─ Target Workspace: {ws}")
        print(f"   └─ Claude File: {expected_path}")

        if not args.dry_run:
            out_file, msg_count = export_to_claude_session(s, args.output_dir, ws)
            print(f"   ✅ Saved session ({msg_count} turns)\n")
        else:
            print("   [DRY RUN - Skipped File Writing]\n")

        migrated_count += 1

    if not args.dry_run:
        print(f"✨ Successfully migrated {migrated_count} session(s) to Claude Code at {args.output_dir}!")
        print("You can now open Claude Code (`claude`) in your project directory and continue your chats seamlessly.")

if __name__ == "__main__":
    main()
