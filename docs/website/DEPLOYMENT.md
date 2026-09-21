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

## Release 20260920-5993d0b08c1c — the hardware page carries the C-F build

Owner instruction: regenerate the hardware page off the current bill of
materials, keeping the device's look and feel. The spec table now names the
build in flight — 6.25-inch 720 × 1560 portrait panel, Orange Pi 5 Plus
(RK3588) with a neural accelerator, 10,000 mAh USB-C pack in a serviceable
tray, hold-to-talk with an I2S microphone, 16 MP camera, 915 MHz LoRa radio
with GNSS, 90 × 182 × 38 mm printed PETG shell, ten parts. The ROCK 5B+,
6.5-inch and "replaceable rear battery pack" claims are gone from the hardware
page, the homepage and every alt/OG string; page structure, voice, palette and
the concept image are unchanged.

Same pipeline: 41 verified files, tarball sha256 `7f6926a3…`, unpacked and
activated on the origin, `--verify`-equivalent digest check passed before the
symlink moved. Verified on the origin and publicly: nginx, natter and
sage-call-relay active, `/`, `/commons/`, `/hardware/` and
`/sage-mission-brief.txt` all 200, and the new spec strings confirmed through
https://sage.ngo. Rollback configuration
`/var/www/sage-website/backup-20260920T095059Z`; prior release
`20260920-4598d379d3a0` remains under `releases/`.

Origin access for follow-up deploys is recorded in SAGE (not in this repo,
which opens publicly in 2027): the host is reachable with the local
`sage-natter` key, and its address is the unproxied `fleet-api.sage.delivery` /
`call.sage.delivery` A record — `sage.ngo` itself is Cloudflare-proxied.

Still outstanding: the concept render regeneration. The owner has not yet
chosen between one antenna and two, or between the detached pack and the tray;
the generation spec is kept in the private `internal-docs` tree.

## Release 20260920-74fe31c6e884 — a map of the four parts on the commons page

Owner suggestion: the commons page could use a diagram. The page now carries one
above the four part descriptions: a labelled stack — SAGE Foundation (signs and
publishes) → SAGE Commons (carries) → community networks (reaches) → SAGE
Lantern — with the boundary drawn inside the last card as a dashed "your private
knowledge" plane. Built from the page's own parts and icons in the site palette,
not a pasted graphic; checked at desktop and 390px widths.

Same pipeline: 41 verified files, tarball sha256 `ac3add4a…`, digest check
passed before the symlink moved. Verified publicly: `/`, `/commons/` and
`/hardware/` return 200 and the diagram's text is present in the served HTML.
Rollback configuration `/var/www/sage-website/backup-20260920T095823Z`; prior
release `20260920-5993d0b08c1c` remains under `releases/`.

## Release 20260920-997818d270e9 — org chart with ownership and licences, and a Foundation page

The commons diagram became an org chart on the owner's direction: the Foundation
at the top, SAGE — the project and SAGE Commons beneath it, then community
networks and the Lantern hanging off the commons. Every box carries who owns it
and the licence it travels under (CERN-OHL-S 2.0 design, GPL-3.0 firmware,
Apache-2.0 application and specifications, CC BY-SA 4.0 documentation, per-item
licences in the commons, MeshCore's own standard), and hovering, focusing or
tapping a box opens a detail drawer that closes on pointer leave, Escape or its
close button. The chart was restyled to the site's language — gradient
surfaces, icon chips, numbered eyebrows, glowing connectors, a soft field behind
it.

New `/foundation/` page: what the steward is and is not, the ownership and
licence table in full, the people behind it, and the open-in-practice summary.
Added to the header and footer navigation; the export verifier now covers four
pages.

Same pipeline: 46 verified files, tarball sha256 `41a339ed…`, digest check
passed before the symlink moved. Verified publicly: `/`, `/commons/`,
`/hardware/` and `/foundation/` all 200, with the new chart text and the
Foundation page content present in the served HTML. Rollback configuration
`/var/www/sage-website/backup-20260920T100835Z`; prior release
`20260920-74fe31c6e884` remains under `releases/`.

Note: this release publishes the per-part licence names on the owner's
instruction. The papers' own licence is stated as "published openly" pending his
call (CC BY, CC BY-SA or CC BY-NC).

## Release 20260920-08d0d307bed5 — the commons parts as a modern flow

Owner direction: "make the org chart super modern — and clear / easy to read".
The five-box chart became one vertical flow. Each part is a numbered row
(01–05) with its icon, a one-line job, sentence-case owner and licence tags, and
a `+` control that expands the row in place for the detail and the boundary
facts. The first row opens by default, so the section never shows an empty
drawer; the side panel, connector arcs, dotted field, glow and the 9.5px
uppercase chips are gone. The flow is capped at 1080px so the copy keeps a
readable measure, and the 390px layout drops the icon column instead of
squeezing the text. The old interactions are kept: hover, focus and click open
a row, Escape closes it, one row is open at a time, and every row is a real
button with `aria-expanded` / `aria-controls`.

Same pipeline: 46 verified files, tarball sha256 `aca3fc4f…`, digest check
passed before the symlink moved. Verified publicly: `/`, `/commons/`,
`/hardware/` and `/foundation/` all 200; the served HTML carries the new
`.flow-` markup and no `.org-` markup is left. Rollback configuration
`/var/www/sage-website/backup-20260920T115921Z`; prior release
`20260920-997818d270e9` remains under `releases/`.

## Release 20260921-8df38b038cb2 — the Foundation's name in the title

Owner ruling: every channel is the Foundation's and the Foundation is the brand
tone — the Commons fabric and the Lantern are content within its story, not
separate brands. The marketing agent checked the live site and found the drift
where the branding rule actually bites: `<title>` and `og:title` read "SAGE
Lantern — Intelligence. For all of us." and `og:site_name` was "SAGE Lantern"
on every page's card, against every account the Foundation now holds.

The title is now "SAGE Foundation — Intelligence. For all of us." with the
tagline kept (it is the line the accounts use), and `og:site_name` is
"SAGE Foundation". The meta description is deliberately untouched — the
marketing agent uses that exact sentence as the bios on the Foundation's social
accounts, so it now changes only through that lane.

Copy on the pages themselves still teaches the old four-part model
(Foundation · Commons-as-knowledge · community networks · Lantern); the
restructure to Foundation → fabric → Lantern is drafted by the marketing agent
and ships in its own release. This one is the brand fix alone.

Same pipeline: 64 verified files, tarball sha256 `47ac1f2a…`, digest check
passed before the symlink moved. This is also the first release to serve
`public/brand/` — four mark colourways as SVG plus PNGs at 1024/512/256 with
their README and WEIGHTS notes. The folder has been in `public/` since it was
added; no release had been built from a tree containing it, which is why the
marketing agent found `sage.ngo/brand/…` returning 404 earlier today. Verified
publicly: `/`, `/commons/`, `/hardware/`, `/foundation/`,
`/brand/lantern-mark-transparent-amber.svg` and `/sage-mission-brief.txt` all
200, and the served HTML carries the new title and `og:site_name`. Rollback
configuration `/var/www/sage-website/backup-20260921T085302Z`; prior release
`20260920-08d0d307bed5` remains under `releases/`.
