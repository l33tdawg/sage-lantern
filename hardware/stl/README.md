# Printable parts

The mechanical workstream has printed real parts. The files are not copied into
this repository yet. This page records what exists, what has been accepted and
what is still a trial, so the folder has an honest starting point.

File existence is not a finished enclosure. A part in this list has a print
result, not a complete device qualification.

## Current print status

| Part | Status | Files |
|---|---|---|
| ROCK 5B+ tub | Finished 263/263. Owner accepted 11 October. | `sage_lantern_rock5b_plus_tub.stl`, `sage_lantern_ROCK5B_PLUS_PRINT.3mf` |
| Firmer bezel | Finished 199/199. Owner says it works well. | `sage_lantern_BEZEL_FIRM_CATCH_TRIAL.stl`, `.3mf` |
| ROCK back cover, captive stand and inlays | Finished 89/89. Owner says the back plate looks good. Retention detail is unverified. | `sage_lantern_rock5b_plus_back_cover.stl`, `_PIP`, `_ASSEMBLY`, `.3mf` |
| V2 antenna collars | Job accepted and preparing 12 October. Completion and fit unknown. | Two V2 collar STLs and a PETG `.3mf` |
| Screen gasket, 2.4 mm crown | CAD checked. Print pending a TPU mount. | `sage_lantern_SCREEN_GASKET_2p4mm_CROWN.stl`, TPU95A `.3mf` |
| Orange Pi and shared parts | Files exist for a tub, bezel, cover, stands, camera guard and coupons. Earlier tub depth was accepted. The set is not a qualified kit. | `outputs/dsi_narrow/`, plus a paired Orange Pi tub in `outputs/rock5b_plus/` |

The accepted firmer bezel is a named variant. It does not replace the default
`dsi_narrow` bezel, and the two should not be mixed in one build without
checking the screen and gasket stack.

## Print settings carried with the accepted parts

These are revision-specific slicing estimates, not universal profiles.

| Part | Material and settings |
|---|---|
| ROCK 5B+ tub | PETG, 0.4 mm nozzle, textured PEI, 0.20 mm layers, 3 walls, 15% infill, no fuzzy skin. Supports on with the supplied blockers, 0.40 mm top gap and 0.50 mm XY gap. Estimate 2h29m55s and 73.61 g. |
| Firmer bezel | Black PETG, screen face down, 0.4 mm nozzle, textured PEI, four local support enforcers. Estimate 50m08s and 14.59 g. |
| ROCK back cover and stand | 0.20 mm layers, 3 walls, smooth, supports off, preserve the supplied bridge settings. Estimate 2h25m31s and 54.49 g. |
| V2 collars | PETG HF, 100% infill, Arachne, supports, brim and fuzzy skin off. 0.20 mm first layer then nineteen 0.10 mm layers. Estimate 9m22s and 0.63 g. |
| Gasket | TPU95A, main nozzle fed externally rather than through an AMS, supports and brim off. Estimate 27m08s and 2.75 g. |

## Planned repository layout

```
hardware/stl/
  rock5b-plus-tub/
    MANIFEST.json
    README.md
    sage_lantern_rock5b_plus_tub.stl
    sage_lantern_ROCK5B_PLUS_PRINT.3mf
  firmer-bezel/
    ...
  rock5b-plus-back-cover/
    ...
```

Use one directory per part family and a new version directory for a geometry
change. Do not overwrite an accepted model in place.

## Manifest fields

| Field | Meaning |
|---|---|
| `part` | Human name and part number |
| `version` | Geometry version |
| `file` | Exact STL or 3MF name |
| `sha256` | File hash |
| `material` | Filament or resin used |
| `printer` | Printer and nozzle |
| `slicer_profile` | Slicer and profile name |
| `orientation` | Print orientation |
| `supports` | Yes or no, and where |
| `units` | Always millimetres |
| `licence` | Licence for the model |
| `source` | Design file or commit |
| `tested` | Print and fit result, or "draft" |

The workstream supplied hashes for the current ROCK tub STL and 3MF, the
accepted firmer bezel and the ROCK cover. They should be checked again when the
files land here.

## Licence direction

The recorded owner direction is:

- hardware exports and drawings: CERN-OHL-S v2;
- parametric Python: GPL-3.0-or-later;
- documentation: CC BY-SA 4.0;
- the name, logo and product identity: a separate trademark policy.

That is a recorded direction, not proof that every file and brand asset already
carries a publishable licence. Check each file before publishing it.

## Before a file goes in this folder

1. Confirm whose design it is and what licence it carries.
2. Print it.
3. Record the printer, material, profile and orientation.
4. Measure the critical dimensions.
5. Fit it to the actual board and housing.
6. Add the manifest and a short README with the result.

The hardware overview is in [`../README.md`](../README.md). The enclosure
research is in
[`UNIT_3_4_ENCLOSURE_STUDY.md`](../../docs/lantern/UNIT_3_4_ENCLOSURE_STUDY.md).
