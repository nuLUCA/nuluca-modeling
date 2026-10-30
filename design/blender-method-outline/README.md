# Blender method outlines

Draft of a member-safe view of Blender methods. Nothing here is wired, and the wheel
does not ship this folder.

## Gap

Blender methods have no outline. `blender_fetch_method` returns the whole bundle, and
the Grasshopper outline refuses Blender bodies by design. A member's agent therefore
has no way to read a Blender method without receiving its source.

## Outline fields

`outline.py` builds `blender-method-outline/v1` from a stored bundle by copying
named fields only.

| Field | Source |
|---|---|
| `id`, `version`, `curation`, `title`, `summary` | Script record |
| `runtime.blender` | Bundle runtime |
| `controls` | Name, unit, kind, type, min, max, default |
| `structure` | Counts of files, lines, functions, classes and assets |
| `blender_surface` | Sorted node types, modifier types and operators the source names |

Left out: source text, comments, docstrings, function and variable names, numeric and
string literals, node order and links, assets and their URLs, lineage, execution notes
and agent notes. A title or summary that reads as code is dropped. `assert_safe`
refuses an outline that carries a private field or source-shaped text.

## Member use

The outline tells a member's agent that a method exists, what it can drive, and which
Blender features it relies on. The agent then writes its own script.

## Still needed

- A nuLUCA catalog operation with a descriptor, scope and positive tariff. The wheel
  shows a granted operation automatically, so it needs no change.
- Search over Blender methods for member agents. `blender_search_methods` returns
  metadata and is not exposed to them today.
- IP review of `blender_surface`. A sorted list of node types says less than a graph,
  but a short method's list may still suggest the technique.
- A policy for contributed titles and summaries, since they are free text.
