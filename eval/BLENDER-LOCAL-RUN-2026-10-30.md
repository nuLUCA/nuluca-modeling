# Local Blender run, October 30, 2026

Blender 5.2.2 LTS, build `d13f752e3b9c`, on an Apple Silicon Mac with 24 GB of memory
and 49% free at the start. The tools were called in process through `BlenderTools`.
A separate test drives the same tools over a real MCP stdio connection. No model
client has done so yet, so a zero-click member journey is not proved.

The model is a small Geometry Nodes field: cubes instanced on a grid with `Count` and
`Realize for export` controls. It tests the tools. It is not a design.

| Call | Seconds | Peak memory (MB) | Result |
|---|---|---|---|
| `blender_run` (build) | 0.78 | 269 | `.blend` saved |
| `blender_inspect` | 0.79 | 260 | 64 instances, both controls listed |
| `blender_preview` | 1.04 | 381 | One 800 × 600 view |
| `blender_vary` (2 and 64) | 0.79 | 271 | 4 and 4,096 instances, unchanged on reopen |
| `blender_export` glb | 0.79 | 261 | 768 of 768 triangles read back, bounds delta 0 |
| `blender_export` usd | 0.79 | 265 | Completed |
| `blender_export` stl | 0.78 | 260 | Completed |
| `blender_export` obj | 0.79 | 259 | Completed |

Time to the first preview, from an empty work folder: 2.81 s. These numbers are from
the final code. An earlier preview from the same model was checked by eye.

## Guard

A script tried each of these against the member's home folder.

| Attempt | Outcome |
|---|---|
| Read a file | Refused |
| List the folder | Refused |
| Write a file | Refused |
| Delete a file | Refused |
| Open a socket | Refused |
| `subprocess.run` | Refused |
| `os.system` | Refused |
| Write in the work folder | Allowed |

The server held a test key in its environment. The key did not appear in any receipt,
log or script in the work folder, and the tests confirm the child environment does
not carry it.

## Confirmed limit

`bpy.ops.wm.save_as_mainfile` wrote a `.blend` outside the work folder, as the README
says it can. Blender's own operators pass beneath the guard.

## Independent review

An adversarial read of the first version found that a script could make the wheel
return or overwrite files outside the work folder through links in its run folder,
that a plain `.blend` could write files during a preview, and that a tampered run
folder could wedge the server. All three are fixed, with regression tests. Script
bypasses of the guard that remain are stated limits: `_posixsubprocess`, walking
`gc` to clear the hook, `os.mkfifo`, and C-level operators such as
`bpy.data.texts.load`.

A millimetre scene exported a 2 mm cube as a 2 m GLB, because the exporter ignores
the unit scale. The export step now scales to metres and compares in metres.

## Not covered

- Windows and Linux. Discovery logic has simulated tests only, and the Windows job
  object change has not run.
- Blender 4.2 to 5.1. These are allowed and reported as `untested`.
- A Mac with no Spotlight index, and a first launch that macOS holds for Gatekeeper.
- A large model, or a run that truly exhausts memory. The stop paths were tested with
  a stand-in process and forced readings.
- Model quality. These runs prove the tools work.
