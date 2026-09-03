# Cursor → Claude Code Migration

Migrate Cursor IDE Composer conversations into Claude Code CLI sessions
while preserving conversation history, timestamps, project context, and
message relationships.
Find the detailed guide below :
https://drive.google.com/file/d/1e87doGNqd9N1Ezl58WZMmMZMJopdUp9C/view?usp=sharing

## How it works
<img width="1024" height="1536" alt="ChatGPT Image Sep 3, 2026, 09_09_30 AM" src="https://github.com/user-attachments/assets/a8aa0396-4b68-4e3e-b8de-9481db7121f7" />



> [!IMPORTANT]
> ### ⚠️ Important Notice: How Workspaces Work in Claude Code (Avoid Confusion!)
> 
> **You will NOT see all your chats in every workspace.**
> 
> Both Cursor and Claude Code strictly isolate chats by project directory:
> - **Cursor** links each chat session to a specific workspace directory via internal hashes (`workspaceStorage`).
> - **Claude Code CLI (`claude`)** discovers sessions by matching your current terminal folder to a project slug in `~/.claude/projects/-<Project-Slug>/`.
> 
> When you open a terminal in `/path/to/project-A` and type `claude`, it **only loads chats belonging to Project A**. It will **not** display chats from Project B.
> 
> | If you want to... | What you should do |
> | :--- | :--- |
> | **See a project's migrated chats** | `cd` into that **exact project directory** before running `claude`. |
> | **Import chats into a different workspace** | Use `--dest /path/to/target-project` to route them there. |
> | **See untitled / scratchpad chats everywhere** | Use `--unspecified-to-all` (symlinks them into all your project workspaces). |


The migration is **non-destructive**: it creates Claude Code session
files and does not intentionally modify or delete Cursor data.

## Features

-   Read Cursor SQLite storage in read-only mode
-   Discover Composer sessions
-   Extract user/assistant messages and timestamps
-   Reconstruct conversation order and `parentUuid` relationships
-   Map Cursor workspaces to repository directories
-   Support `.code-workspace` projects
-   Generate Claude Code `.jsonl` session files
-   Dry-run before writing
-   Migrate one session, one workspace, or all sessions
-   Python standard library only

## Requirements

-   Python 3.8+
-   Cursor installed/used on the machine containing the conversations
-   Claude Code installed and available as `claude`

Check: `python3 --version` and `claude --version`.

## Cursor storage

Typical Cursor User directories:

  OS        Path
  --------- ---------------------------------------------
  macOS     `~/Library/Application Support/Cursor/User`
  Linux     `~/.config/Cursor/User`
  Windows   `%APPDATA%/Cursor/User`

Important data is commonly found in `globalStorage/state.vscdb`,
including records such as:

``` text
composerData:<composerId>
bubbleId:<composerId>:<bubbleId>
```

Workspace-specific information can be found under
`workspaceStorage/<hash>/`, including `workspace.json` and
`state.vscdb`.

## Installation

Clone the repository and enter it:

``` bash
git clone <YOUR_REPOSITORY_URL>
cd <YOUR_REPOSITORY_DIRECTORY>
```

The repository should contain:

``` text
cursor-to-claude/
├── migrate_cursor_to_claude.py
└── README.md
```

No `pip install` is required when using only the Python standard
library.

## Usage

### 1. List Cursor chats

``` bash
python3 migrate_cursor_to_claude.py --list
```

Use this to discover Composer IDs, titles, workspaces, and available
sessions.

### 2. Dry-run a workspace

``` bash
python3 migrate_cursor_to_claude.py \
  --workspace /path/to/your/project \
  --dry-run
```

Example:

``` bash
python3 migrate_cursor_to_claude.py \
  --workspace /Users/vikrant/Developer/work-repos/backend_revamp \
  --dry-run
```

A dry run should show matched sessions, message counts, and target
destinations without writing migration files.

### 3. Migrate a workspace

``` bash
python3 migrate_cursor_to_claude.py \
  --workspace /path/to/your/project
```

### 4. Migrate one session

First find the Composer ID with `--list`, then:

``` bash
python3 migrate_cursor_to_claude.py \
  --session-id <composer-id> \
  --workspace /path/to/your/project
```

### 5. Migrate all sessions

``` bash
python3 migrate_cursor_to_claude.py --all

#unspecified session ---- (new)
--unspecified-to-all: Exports all 103 untitled/unspecified sessions and symlinks them into all existing Claude Code project workspaces.
--unspecified-only --workspace <path>: Exports all 103 untitled sessions into a single chosen workspace.

```

For a large history, run `--list` and/or a dry run first.

## What gets generated?

Claude Code sessions are written under:

``` text
~/.claude/projects/<project-slug>/
```

For example:

``` text
/Users/vikrant/Developer/work-repos/backend_revamp
        ↓
-Users-vikrant-Developer-work-repos-backend-revamp
```

Result:

``` text
~/.claude/projects/
└── -Users-vikrant-Developer-work-repos-backend-revamp/
    ├── <composer-id-1>.jsonl
    ├── <composer-id-2>.jsonl
    └── ...
```

Each JSONL file represents one migrated conversation.

## Message conversion

Cursor stores individual conversation turns as bubbles. The script
converts them into Claude Code session records. A simplified user record
looks like:

``` json
{
  "type": "user",
  "sessionId": "cc94...",
  "uuid": "msg-1",
  "parentUuid": null,
  "timestamp": "2026-09-02T10:00:00.000Z",
  "cwd": "/path/to/repo",
  "message": {
    "role": "user",
    "content": "How do we migrate this service?"
  }
}
```

Assistant messages use an array of content blocks:

``` json
{
  "type": "assistant",
  "uuid": "msg-2",
  "parentUuid": "msg-1",
  "message": {
    "role": "assistant",
    "content": [
      {"type": "text", "text": "Here is the plan..."}
    ]
  }
}
```

`parentUuid` is important because it links the conversation into a
chain:

``` text
User 1 → Assistant 1 → User 2 → Assistant 2
```

## `.code-workspace` support

Cursor/VS Code can use multi-root workspaces:

``` text
my-project.code-workspace
├── frontend
└── backend
```

Claude Code is normally launched from an actual directory, for example:

``` bash
cd backend
claude
```

The migration resolves the workspace to the appropriate
repository/project directory and can create aliases when needed for
workspace access.

## Verify the migration

After migrating:

``` bash
cd /path/to/your/project
claude
```

Then use:

``` text
/resume
```

You should see the migrated Cursor conversations in Claude Code's
session switcher.

You can also inspect the generated files:

``` bash
ls -lah ~/.claude/projects/
ls -lah ~/.claude/projects/<project-slug>/
cat ~/.claude/projects/<project-slug>/<session-id>.jsonl
```

## Safety

### Read-only Cursor access

The script should open Cursor SQLite databases using read-only mode:

``` text
file:<path>?mode=ro
```

### Non-destructive

The migration creates a reconstructed copy for Claude Code. Original
Cursor conversations remain in Cursor.

### Recommended workflow

``` bash
# Discover
python3 migrate_cursor_to_claude.py --list

# Preview
python3 migrate_cursor_to_claude.py --workspace /path/to/project --dry-run

# Migrate
python3 migrate_cursor_to_claude.py --workspace /path/to/project

# Verify
cd /path/to/project
claude
# Then: /resume
```

## Troubleshooting

### No sessions found

Confirm Cursor's storage exists and that Cursor has been used on the
machine.

### Workspace not detected

Run:

``` bash
python3 migrate_cursor_to_claude.py --list
```

Then verify the expected repository/workspace is associated with the
Composer.

### Session does not appear in `/resume`

Check that:

1.  A `.jsonl` file was created.
2.  It is under the correct `~/.claude/projects/<project-slug>/`
    directory.
3.  Claude Code is launched from the matching repository.
4.  The JSONL contains valid JSON on every line.
5.  `sessionId`, `uuid`, and `parentUuid` values are valid.

## Architecture

``` text
┌─────────────────────┐
│ Cursor IDE          │
│ SQLite storage      │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Discovery           │
│ Composer sessions   │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Extraction          │
│ Messages / metadata │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Reconstruction      │
│ Message chain       │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Transformation      │
│ Claude JSONL format │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ ~/.claude/projects/ │
└──────────┬──────────┘
           │
           ▼
        /resume
```

## Contributing

Useful areas for improvement include workspace detection, additional
Cursor storage formats, migration validation, duplicate-session
detection, malformed-record handling, and automated tests.

## Disclaimer

Cursor and Claude Code may change their internal storage formats between
versions. Use dry-run mode first and keep backups of important data.
This project reconstructs/copies conversations; it is not intended to
delete or modify original Cursor history.


