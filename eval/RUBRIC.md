# Member modeling paired rubric — frozen before runs

There are 12 runs: three briefs, two conditions, two repeats. Label outputs
with random run IDs before blind review; hide condition, cloud calls, and run
order from the reviewer. Give the reviewer only the saved definition, a cold
first-open solve report, anonymous design note and control-change evidence.

## Hard gates per run

1. The saved `.ghx` opens in a cold Rhino process and solves with zero runtime
   errors and zero null output ports used by the final design.
2. Every control named in the brief exists and changing it changes the stated
   geometry in the intended direction.
3. Specified outer dimensions are within 1 mm at the default values.
4. A readable `.json` author source and `.ghx` are retained, with matching run
   identity and a source hash.

A run passes only when all four gates pass. Record failures as observed; do not
impute a pass from a transcript or a warm solve.

## Blind scored measures

The reviewer scores each item from 0 to 5 with a short reason:

- Node grouping and names make the method navigable.
- Inputs are exposed as useful sliders or value lists at the definition edge.
- An architect can inspect and edit the method without reverse engineering it.
- The design meets the brief without decorative geometry that obscures the
  stated purpose.
- For B3 only, the biological correspondence, departures and comparison to
  the uniform baseline are honest and specific. Record this as `N/A` for B1/B2.

Also record elapsed wall time to the first valid solve, total local/cloud tool
calls, cloud member charges, and raw provider cost. Keep source and blinded
review files separately so the reviewer cannot infer condition.

## Decision rule

Condition B supports a "better" claim only if it passes more hard gates than A
in at least two of the three briefs and scores no worse on the rubric in every
brief. Otherwise report "not established" and retain the losses. Compare
paired repeats by brief, with the median score and median time for each
condition. A strong design with a failed cold reopen still fails its gate.
