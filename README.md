# nuLUCA Modeling

`nuluca-modeling` connects Claude Code or Codex to nuLUCA Modeling and the
local JsonToGH Grasshopper bridge.

Ask your Claude Code or Codex agent:

> Set up nuLUCA modeling for me. Walk me through any approval you need, one step at a time.

## Agent setup

Follow [INSTALL.md](https://github.com/nuLUCA/nuluca-modeling/blob/main/INSTALL.md).
It covers the setup command, a manual fallback, unblocking the plugin and
confirming the bridge with `nuluca_status`, for any MCP client.

The local shell currently uses a Modeling key. Keep it in
`NULUCA_AGENT_CONNECT_KEY` locally; never ask the member to paste it into chat
or print it. Guide them through any required action on their signed-in
Connected agents page, explaining the next click and waiting for them.
Run the installation command yourself:

```sh
uvx nuluca-modeling setup --environment staging
```

Never quit, kill or relaunch the member's Rhino. Ask the member to save their
work and restart Rhino themselves, wait for them, then call `nuluca_status`.

The plugin download works on staging for invited pilot members. Setup defaults
to production, which reports that the download is unavailable until it is
deployed there. `NULUCA_AGENT_CONNECT_URL` selects another HTTPS service origin
when `--environment` is not given. Use a key for the chosen environment.

The shell exposes granted cloud operations and local modeling tools. It does
not expose Python or command execution.
