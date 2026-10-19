# Printable parts

There are no STL files here yet. Nothing has been printed, and the enclosure
programme is still before its first-article print and tolerance report. A model
without that evidence is a draft, not a release.

This folder exists so the files have one obvious home when they are ready.

## Planned layout

```
hardware/stl/
  enclosure-v1/
    MANIFEST.json
    README.md
    body.stl
    bezel.stl
  brackets/
    ...
```

Use one directory per part family and a new version directory for a geometry
change. Do not overwrite a released model in place.

## Manifest fields

Every published part should have a sibling `MANIFEST.json` with:

| Field | Meaning |
| --- | --- |
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

## Licence

Project-authored models are Apache-2.0 unless the part says otherwise.
Third-party models keep their own licence. Do not publish a model whose licence
forbids redistribution.

## Before publishing

1. Print the part.
2. Record the printer, material, profile and orientation.
3. Measure the critical dimensions and record the tolerance.
4. Fit it to the actual board and enclosure.
5. Add the manifest and a short README with the result.

The enclosure study is in
[`UNIT_3_4_ENCLOSURE_STUDY.md`](../../docs/lantern/UNIT_3_4_ENCLOSURE_STUDY.md).
The hardware overview is in [`../README.md`](../README.md).
