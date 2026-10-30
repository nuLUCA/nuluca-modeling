# nuLUCA Modeling

`nuluca-modeling` connects Claude Code, Codex or another MCP client to nuLUCA
Modeling and the local nuLUCA for Grasshopper plugin.

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

## Blender

With Blender 4.2 or newer installed, your agent can model without Rhino. Blender 5.2
is tested. The tools find Blender on macOS and Windows, or use
`NULUCA_BLENDER_PATH`, and they never install it. Call `blender_status`, then
`blender_guide`.

Each call starts a separate background Blender. A Blender you have open is left
alone, and the wheel never quits, kills or relaunches an application. The wheel
stops only a background Blender it started itself.

`blender_run` executes Python written by your agent, as you, on your machine. Files
stay in the modeling work folder (`NULUCA_MODELING_WORK_ROOT`, by default
`~/nuluca-modeling/work`). Your nuLUCA key is not passed to Blender. A guard refuses
ordinary Python writes outside that folder, reads elsewhere in your home folder,
network calls and starting other programs.

The guard catches mistakes and is not a sandbox. Blender's own file operators and
native code pass beneath it, and a script written to defeat it can get past it. Such
a script could read anything you can, including the client configuration that holds
your key, so show yourself any script you did not ask for before it runs.

Set `NULUCA_MODELING_BLENDER=fixed` to remove `blender_run` and `blender_write`,
which leaves inspection, variation, export and preview. These open a `.blend` with
scripts off. Set it to `off` to remove every Blender tool.

## Tool surface

The shell exposes granted cloud operations and local modeling tools. `blender_run`
is its one tool that executes code you supply, inside a background Blender. The
Grasshopper tools have no Python or command tool of their own. Definitions can still
run code, as any Grasshopper file with script components can, and agent code
execution is on in the plugin by default. Rhino runs with the member's full rights,
outside the agent's sandbox. `INSTALL.md` covers what this means and the off switch.
