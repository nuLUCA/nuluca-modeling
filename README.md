# nuLUCA Modeling

`nuluca-modeling` connects Claude Code or Codex to nuLUCA Modeling and the
local JsonToGH Grasshopper bridge.

Create a Modeling key from Connected agents, save it in
`NULUCA_AGENT_CONNECT_KEY`, then run:

```sh
uvx nuluca-modeling setup
```

Setup downloads the member-only JsonToGH plugin for the current platform,
verifies its SHA-256 checksum, configures both coding clients, and never prints
the key. Restart Rhino, then call `nuluca_status` from your agent.

Use `--client claude` or `--client codex` to configure one client, `--dry-run`
to inspect changes, and `--uninstall` to remove files added by setup. Advanced
installations can provide a local or HTTPS plugin archive with
`--plugin-source`; its checksum is still required.

The shell exposes granted cloud operations and local modeling tools. It does
not expose Python or command execution.
