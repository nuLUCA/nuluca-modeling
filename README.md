# nuLUCA Modeling

`nuluca-modeling` connects Claude Code or Codex to nuLUCA Modeling and the
local JsonToGH Grasshopper bridge.

Ask your Claude Code or Codex agent:

> Set up nuLUCA modeling for me. Walk me through any approval you need, one step at a time.

## Agent setup

The local shell currently uses a Modeling key. Keep it in
`NULUCA_AGENT_CONNECT_KEY` locally; never ask the member to paste it into chat
or print it. Guide them through any required action on their signed-in
Connected agents page, explaining the next click and waiting for them.
Run the installation command yourself:

```sh
uvx nuluca-modeling setup
```

Setup downloads the member-only JsonToGH plugin for the current platform,
verifies its SHA-256 checksum, and configures both coding clients. Ask the
member to save their work and restart Rhino, then call `nuluca_status` yourself
to check the connection and bridge.

Setup uses the production service by default. For an authorized staging pilot,
set `NULUCA_AGENT_CONNECT_URL=https://nuluca-backend-staging.up.railway.app`
in the setup process. The override applies to the plugin download and both
clients' saved configuration. Use a key for that environment. Setup requires
an HTTPS service origin without a path, query, fragment or embedded credentials.

Use `--client claude` or `--client codex` to configure one client, `--dry-run`
to inspect changes, and `--uninstall` to remove files added by setup. Advanced
installations can provide a local or HTTPS plugin archive with
`--plugin-source`; its checksum is still required.

The shell exposes granted cloud operations and local modeling tools. It does
not expose Python or command execution.
