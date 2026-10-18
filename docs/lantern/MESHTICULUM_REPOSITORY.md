# Meshticulum repository

Meshticulum now has a separate private repository: https://github.com/l33tdawg/meshticulum.

The local checkout is `/Users/l33tdawg/nodejs-projects/meshticulum`. Its initial source snapshot is commit `2f280da19361e75138a49c5e8dadef8193f6e21e` on `main`.

New Meshticulum development belongs in that repository. The existing SAGE experimental sources are preserved as the current integration snapshot; changing SAGE to consume a pinned standalone dependency is a later reviewed integration step. Preserve accepted writer ownership and source pins during that transition.

The standalone package contains transport contracts, scheduler, accepted flow, authenticated wire/journals/workers, public resources, compact envelope codecs, firmware/lab tooling and optional SAGE adapters. It retains Apache-2.0 licensing and source provenance. Validation passed 1,676 SDK-free controls and 258 optional SDK source controls; an installed-wheel smoke check imported all 37 core modules without SAGE.

Physical mixed-path qualification, native private admission, reproducible firmware packaging and fair matched hardware benchmarks remain open. No device dispatch, installation or production change accompanied the extraction. The coordinated C window was returned unused and the model lane acknowledged its closure.

Local extraction evidence is retained under `work/meshticulum-repo-20261010/`; original SAGE-side checkpoint receipts remain in their original directories.
