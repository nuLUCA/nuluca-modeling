# Local Blender run, October 30, 2026

Blender 5.2.2 LTS, build `d13f752e3b9c`, on an Apple Silicon Mac with 24 GB of memory
and 40% free at the start. The tools were called in process through `BlenderTools`.
No MCP client was involved, so a member's agent has not driven this yet.

The model is a small Geometry Nodes field: cubes instanced on a grid with `Count` and
`Realize for export` controls. It tests the tools. It is not a design.

| Call | Seconds | Peak memory (MB) | Result |
|---|---|---|---|
| `blender_run` (build) | 1.04 | 265 | `.blend` saved |
| `blender_inspect` | 0.78 | 259 | 64 instances, both controls listed |
| `blender_preview` | 1.02 | 382 | One 800 × 600 view, checked by eye |
| `blender_vary` (2 and 64) | 1.03 | 268 | 4 and 4,096 instances, unchanged on reopen |
| `blender_export` glb | 0.78 | 264 | 768 triangles read back of 768, bounds delta 0 |
| `blender_export` usd | 0.78 | 266 | Completed |
| `blender_export` stl | 0.78 | 257 | Completed |
| `blender_export` obj | 0.77 | 257 | Completed |

Time to the first preview, from an empty work folder: 3.41 s.

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

## Not covered

- Windows and Linux. Discovery logic has simulated tests only, and the Windows job
  object change has not run.
- Blender 4.2 to 5.1. These are allowed and reported as `untested`.
- A Mac with no Spotlight index, and a first launch that macOS holds for Gatekeeper.
- A large model, or a run that truly exhausts memory. The stop paths were tested with
  a stand-in process and forced readings.
- Model quality. These runs prove the tools work.
