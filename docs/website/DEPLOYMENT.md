# Deployment validation — 9 September 2026

Host: existing SAGE Natter origin, identified from the SAGE v11.5 deployment plan and pinned SSH host key.
Public hostname: sage.ngo, as explicitly reaffirmed by the owner. An initial local resolver gave an empty A answer; Cloudflare (1.1.1.1) and Google (8.8.8.8) both confirmed configured proxied A/AAAA records. No DNS mutations made.

Production export passes. Full TypeScript check passes. Authored-source lint passes. Static HTML check verifies one H1, all internal section anchors, all local asset references, email contact, and illustration labels. npm audit reports zero vulnerabilities after compatible framework updates and a patched sharp override. The unused upstream shadcn catalog has existing lint diagnostics and is outside the authored-source lint target. No browser interaction/visual test was requested or claimed.

A certificate was initially prepared for natter.sage.delivery but no website release was activated there. The user reaffirmed sage.ngo and the certificate route was retargeted to that domain. Site uses a dedicated 127.0.0.1:9444 HTTPS listener behind the existing nginx SNI router. Existing call relay, Natter and Fleet routes are retained.

Final release: `20260909-638366e018ff`, 39 verified files. Activated on sage.ngo with nginx validation and configuration rollback backup. Added the rural family connectivity concept to the homepage; MeshCore and planned Starlink messaging appear on both homepage and hardware page. All exported images use static URLs. Original Natter service routes remain in place.

Pitch deck companion: `docs/SAGE_Foundation_Updated_September_2026.pptx` and `.pdf`. The 26-slide edition uses Geist/Georgia, the navy/amber website palette, selected Commons mark and current concept imagery. Added hardware slide 5 and strengthened network slide 21. Original September deck preserved. Same copywriting skill applied across visible slide copy. Speculative radio modifications are not added to the public promise. PPTX package, geometry, fonts and reimport passed; all slide renders inspected. PDF is a fixed visual sharing copy; PPTX retains editable text and diagrams.

## Release 20260920-ab8cc68b10b3 — the Commons page

Added `/commons/`: the distinction the owner asked for, in one place. Four parts
(SAGE Foundation as a steward in formation; SAGE Commons as the shared knowledge;
community networks as the way it travels, MeshCore radio plus planned backhaul;
the Lantern as the device at the edge), an ownership table (private knowledge,
the shared commons, the radio standard, signed releases, the device), why the
separation matters, and an honest status note. Nav and footer gained "The
Commons"; the homepage commons section links to it; the export verifier now
covers the third page.

Copy was reviewed with the writing skill against the existing site voice: the
third part is "Community networks" rather than a new term, and the closing line
matches the homepage's register. Claims stay inside what is true today —
Foundation in formation, nothing a live service, backhaul planned.

Packaged by the new `scripts/build-release.py` (41 verified files, tarball
sha256 `c1355a38…`) and activated by `deploy/install-release.py`. Verified on the
origin and publicly: nginx, natter and sage-call-relay active, call-relay
healthz 200, and `/`, `/commons/`, `/hardware/` all 200 on
`https://sage.ngo`. Rollback configuration:
`/var/www/sage-website/backup-20260920T063743Z`; the previous release
`20260909-638366e018ff` stays under `releases/` and can be re-activated by
pointing `current` back at it.

## Release 20260920-e7182555e702 — measurements, not a December date

Owner-confirmed correction relayed from the hardware agent: the live fleet is
units C–F (G and H wait until C–F are printed and locked), and no fleet date is
being worked to. The homepage roadmap and the downloadable mission brief
therefore withdraw the December "six complete demonstration Lanterns" target;
they now say first-article prints and a measured tolerance report come before
locked hardware choices and before any dates. The January 2027 announcement
target is unchanged.

Same pipeline: 41 verified files, tarball sha256 `b6919551…`, activated on the
origin with `/`, `/commons/`, `/hardware/` and `/sage-mission-brief.txt` all 200
and the corrected copy confirmed through `https://sage.ngo`. Rollback
configuration `/var/www/sage-website/backup-20260920T072849Z`; prior release
`20260920-ab8cc68b10b3` remains on disk.
