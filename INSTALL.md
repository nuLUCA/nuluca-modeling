# Install nuLUCA Modeling

This guide is written for the coding agent doing the install. It covers Rhino 8
on macOS and Windows. Any MCP client can use it.

The plugin download currently works only on staging, for invited pilot members.
Production is the setup default, and it returns a clear error until the download
route is deployed there. Use `--environment staging` with a staging key.

## Rules for agents

- Never quit, kill or relaunch the member's Rhino. The member restarts it.
- Ask the member to save their work before they restart.
- Never ask the member to paste a key into chat, and never print a key.

## Prerequisites

- Rhino 8 with Grasshopper
- [uv](https://docs.astral.sh/uv/getting-started/installation/), which provides `uvx`
- A Modeling key from the Connected agents page in nuLUCA

## Setup

Run this in the member's own terminal or a protected credential prompt, so the
key stays out of the conversation.

macOS:

```sh
read -rs NULUCA_AGENT_CONNECT_KEY
export NULUCA_AGENT_CONNECT_KEY
uvx nuluca-modeling setup --environment staging
```

Windows PowerShell:

```powershell
$key = Read-Host -AsSecureString
$env:NULUCA_AGENT_CONNECT_KEY = [System.Net.NetworkCredential]::new('', $key).Password
uvx nuluca-modeling setup --environment staging
```

Setup downloads the plugin for this computer, checks its SHA-256 checksum,
copies it into Grasshopper's Libraries folder and registers the MCP server. It
configures Claude Code and Codex when it finds them.

Any other MCP client: add `--client other`. Setup changes no client files and
returns `manual_client_entry`, which holds the command, arguments and environment
variable names for a stdio server. Store the key in that client's protected
credential entry.

| Option | Effect |
|---|---|
| `--client claude`, `--client codex` | Configure only the named client |
| `--dry-run` | Show what would change |
| `--uninstall` | Remove what setup added and restore what it replaced |
| `--plugin-source` | Install from a local or HTTPS archive; a checksum is still required |
| `--package-spec` | Make the client start a local wheel, for testing before release |

If setup reports `Rhino may be using it`, ask the member to close Rhino and run
setup again. Windows locks a loaded plugin; macOS does not.

If Libraries already holds a `Newtonsoft.Json.dll`, setup leaves it there and
says so, because other plugins may share it.

## Restart

Ask the member to save their work, restart Rhino, and open Grasshopper. Wait for
them to confirm.

## Confirmation

Call `nuluca_status`. A working install returns `bridge.status` of `ok`, the
protocol `jsontogh-bridge/1` and a `plugin_version`.

| Message | Next step |
|---|---|
| `Start Rhino and the JsonToGH plugin first.` | The plugin has not loaded yet. Ask the member to open Grasshopper. |
| `No running Rhino bridge is available.` | Ask the member to open Grasshopper. If it is open, check the Libraries folder below. |
| `Select one running Rhino bridge` | Two copies of Rhino are open. Set `NULUCA_GH_BRIDGE_PORT` or ask the member to close one. |

Before importing a definition, call `canvas_preflight` to see which plugins it
needs and which are missing. `canvas_import` will not clear a canvas that has
objects on it unless the member agrees.

## Manual install

Use this when setup cannot reach the download. Unzip the archive and copy
`JsonToGH_Fresh.gha` into the Libraries folder. Copy `Newtonsoft.Json.dll` too,
unless the folder already has one.

| System | Libraries folder |
|---|---|
| macOS | `~/Library/Application Support/McNeel/Rhinoceros/8.0/Plug-ins/Grasshopper (b45a29b1-4343-4035-989e-044e8580d9cf)/Libraries` |
| Windows | `%APPDATA%\Grasshopper\Libraries` |

The operating system marks downloaded files as blocked, and Rhino will not load
a blocked plugin. Clear the mark on both files.

macOS:

```sh
cd "$HOME/Library/Application Support/McNeel/Rhinoceros/8.0/Plug-ins/Grasshopper (b45a29b1-4343-4035-989e-044e8580d9cf)/Libraries"
xattr -d com.apple.quarantine JsonToGH_Fresh.gha Newtonsoft.Json.dll
```

`xattr` reports an error for a file that has no mark. That error is harmless.

Windows PowerShell:

```powershell
cd "$env:APPDATA\Grasshopper\Libraries"
Unblock-File JsonToGH_Fresh.gha, Newtonsoft.Json.dll
```

Then follow Restart and Confirmation above.
