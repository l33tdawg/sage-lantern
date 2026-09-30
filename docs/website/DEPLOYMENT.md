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

## Config-only change, 2026-09-21 — one cache policy, and one stray URL

Owner report: "the top links are not consistent on some of the pages", and a
link seen resolving to `https://sage.ngo/hardware/index.txt/`. Reproduced in a
real browser rather than inferred, and it is two separate defects in the nginx
config, not inconsistent markup — the served HTML for all four pages carries
the same six-link header.

**1. Pages had no cache directive, so a browser could reuse an old one.**
`location = /index.html { add_header Cache-Control "no-cache"; }` matched the
homepage only. `/commons/`, `/hardware/` and `/foundation/` returned no
`Cache-Control`, no `ETag` and a `Last-Modified: 1970-01-01` placeholder, so
the browser was free to reuse whatever it held — including the RSC payload
under each route (`/commons/index.txt`), which is what the client router
renders from during soft navigation. A stale `/commons/` with only five header
links (no "Foundation") was reproduced in Chrome, and cleared on reload: the
deployed page has had six links all along. Any visitor who browsed before a
release can therefore see an old page next to a new one, which is exactly the
inconsistency reported.

**2. The same `add_header` in a `location` was also suppressing the security
headers.** nginx does not inherit `add_header` into a location that sets its
own, so `/` and `/index.html` were served without `X-Content-Type-Options`,
`Referrer-Policy`, `Permissions-Policy` and `X-Frame-Options`, while every
other page had them.

**3. `…/index.txt/` 404'd.** `/hardware/index.txt` is a real file (the route
payload `vinext` fetches for soft navigation, publicly readable, `text/plain`)
and must keep serving. `/hardware/index.txt/` — the form in the report — is not
a file, was not a directory, and fell through to a bare nginx 404.

Fix, in `apps/sage-website/deploy/30-sage-website.conf`: cache policy moves to
one `map` plus a single server-level `add_header` — `no-cache` for pages and
payloads, `public, max-age=31536000, immutable` for content-hashed
`/_next/static/` assets, one week for `images/` and `brand/` — so the security
headers can no longer be cancelled by a location. `absolute_redirect off` keeps
the origin's internal `127.0.0.1:9444` out of any redirect. A
`location ~ ^/(.+/)index\.txt/$ { return 301 /$1; }` sends the stray
trailing-slash payload URL to the page instead of a 404, while the payload
itself is untouched so the client router still works.

Applied as a config-only change, no new release: config backed up to
`/root/30-sage-website.conf.bak-20260921` (and `…b` after the redirect
correction), `nginx -t` passed before each reload, and the previous file is
restored automatically if the test fails. Verified publicly: `/`, `/commons/`,
`/hardware/`, `/foundation/` all 200; every page now returns `Cache-Control:
no-cache` **and** the four security headers; a hashed asset returns the
immutable cache header; `/hardware/index.txt/` returns `301 →
https://sage.ngo/hardware/`; `/hardware/index.txt` still returns 200
`text/plain`; soft navigation between all four pages still works from a real
browser.

The React hydration errors seen during this investigation (minified #418) are
diagnosed and fixed in the next entry.

## Release 20260921-bbed3af474b5 — Cloudflare was rewriting the email addresses

The hydration error from the entry above: the live homepage logged React #418
("server rendered text did not match the client") on a fresh load, while the
dev server and the local static export were both clean. That difference is what
identified the cause — the served HTML is not the HTML we built.

Cloudflare's **Email Address Obfuscation** rewrites every address in the HTML
after the export is final: the text becomes
`<span class="__cf_email__" data-cfemail="…">[email protected]</span>` and a
`mailto:` href becomes `/cdn-cgi/l/email-protection#…`. React then hydrates the
text it rendered against text Cloudflare substituted, and fails. Confirmed by
comparing pages: the live HTML carried `__cf_email__` spans and protected hrefs,
the built files carried neither.

Fix: `components/email-off.tsx` renders the address inside the documented
Cloudflare `email_off` comment markers, which Cloudflare leaves alone. React
cannot emit a comment node, so the address is written as raw markup — the same
string on the server and in the client, which is what hydration compares. Used
in the three places where an address appears as text (homepage contact link and
role dialog, Foundation people section). Obfuscated `mailto:` hrefs are left
alone: an attribute is not a hydration failure, and Cloudflare's decode script
restores it in the browser.

Same pipeline: 64 verified files, tarball sha256 `76ab865c…`, digest check
passed before the symlink moved. Verified with fresh browser tabs and
cache-busted URLs: **zero console errors** on `/`, `/commons/`, `/hardware/` and
`/foundation/` (the homepage previously logged one on every load); the address
renders as plain text and the `mailto:` href is intact in the DOM; Cloudflare no
longer injects `__cf_email__` markup. Rollback configuration
`/var/www/sage-website/backup-20260921T090801Z`; prior release
`20260921-8df38b038cb2` remains under `releases/`.

Not done, and worth a decision later: the cleanest long-term answer is to turn
Email Address Obfuscation off for the zone, since the address is published
openly anyway; the comment markers then become harmless. Left as the owner's
call rather than a unilateral account change.

## Release 20260921-e61852cb6b4f — the published licence set withdrawn

Owner instruction 2026-09-21 ("the license table take it out for now"), after
the comms agent flagged that the live site named a per-part licence set the
hardware side says is not signed off and must not be published yet.

Withdrawn from public copy: CERN-OHL-S 2.0, GPL-3.0, Apache-2.0 and CC BY-SA
4.0. Kept: every ownership statement, the trademark line, the per-item
licence *mechanism* on the commons diagram, and the site's existing
"published with each release as they are signed off" wording, which now carries
the licence rows in the `/foundation/` ownership table and the two `/commons/`
chips. The "open, in practice" paragraph states the intent (hardware, firmware,
application and specifications published under open licences as they are signed
off) without naming a licence that has not been signed. The copy is the
marketing lane's to rewrite; this release is the withdrawal, not a new drafting
position.

`scripts/verify-export.py` pinned the old claims as a positive assertion, so it
now pins the withdrawal instead: none of the four identifiers may appear on
`/foundation/` or `/commons/`, and "signed off" must appear on `/foundation/`.
A positive assertion goes back only when a signed-off licence returns.

Release `20260921-e61852cb6b4f`, built from commit `e61852c`, 64 verified files,
tarball sha256 `af59c558fa1d5659c04b2183df94e535d76074c6921a36a904965789ab77f6d4`.
Activated on the origin with nginx validation and configuration rollback
backup `/var/www/sage-website/backup-20260921T091225Z`; prior release
`20260921-8df38b038cb2` stays under `releases/`. Verified live: `/`,
`/commons/`, `/foundation/` and `/hardware/` all 200, no licence identifier
present on any of the four, and the new wording served.

## Zone setting, 2026-09-21 — Email Address Obfuscation switched off

Follow-up to the hydration fix, on the owner's instruction ("turn it off").
Cloudflare's **Email Address Obfuscation** is now **off** for `sage.ngo`, so
the rewriting described in release `20260921-bbed3af474b5` no longer happens at
all: addresses are served as written, `mailto:` hrefs stay `mailto:` hrefs, and
the injected decode script is gone.

Changed in the dashboard rather than the API, because the account has no stored
API token on this machine and the classic `/scrape-shield` route no longer
exists in this zone's dashboard. The setting now lives in
**Security → Settings → search "obfuscation" → Email Address Obfuscation**, in
the "Client side abuse" group. The switch was verified off in the page's own
accessibility tree (`switch ... Value: 0`) after the change.

Verified at the edge after a twelve-second settle: `/`, `/commons/`,
`/hardware/` and `/foundation/` all serve **zero** `__cf_email__` spans and
**zero** `/cdn-cgi/l/email-protection` links, with plain `mailto:` hrefs on all
four, and fresh browser tabs report no console errors.

`components/email-off.tsx` is deliberately kept. It is now belt-and-braces —
inert while obfuscation is off — and it stops the same hydration failure
returning silently if the setting is ever switched back on.

## Release 20260930-cf2648497c28 — MeshCore in one line, and what a mesh reaches

Owner instruction, after the comms lane asked for software sign-off on
capability facts for a public explainer lane. Most of the signed-off lines were
already carried by the site — the 915 MHz wording, the answer-path framing in
the FAQ, no LoRaWAN "AS923" vocabulary, and no public use of the offered
"MeshCore Commons" name — but the network section never explained what MeshCore
is and never said the mesh's reach plainly. Two sentences were added to the
homepage network section: MeshCore as an open-source LoRa mesh protocol and
firmware whose nodes relay for each other, and "A mesh reaches as far as the
people, radios and power in it — relay by relay, no tower, and no promise of
coverage."

Built from commit `cf26484`, 64 verified files, tarball sha256
`0d1185641633459c45aec646bcb1b876cd48611d647ae3b25161626145aae57e`. Activated on
the origin with nginx validation and configuration rollback backup
`/var/www/sage-website/backup-20260930T114623Z`; prior release
`20260921-e61852cb6b4f` stays under `releases/`. Verified live: `/`, `/commons/`,
`/hardware/` and `/foundation/` all 200, both new lines present in the served
HTML, and nginx, natter and sage-call-relay active.

## Release 20260930-9b7aa43cbaea — the pack is gone, two antennas, and the copy follows

Owner instruction: *"we need new renders for the device cause we've removed the
battery pack and stuff"*, then the antenna layout — two external antennas
(taller LoRa, shorter GNSS) plus a third antenna for the second LoRa radio
inside the shell, which must never appear in a render.

**The render.** Produced with **Gemini 2.5 Flash Image** ("Nano Banana") on
Vertex AI through the existing `gcloud` login (no new API key), using the live
`hardware.jpg` as the design reference so the device's look and feel stayed the
invariant. The pack and the open battery bay were removed in two reference-based
edit passes; the model proved unreliable at placing an antenna pair on *both*
device views (it repeatedly moved the pair from one view to the other), so the
final antenna work is a locally composited, pixel-aligned merge of two variants,
with the front's GNSS whip shortened to read shorter than the LoRa whip. Master,
prompts and provenance: `work/hardware-render-20260930/` (private work tree).

**The copy.** The page no longer promises a bundled pack: power is a USB-C
supply the user brings (D-015, hardware-only units), the committed compute is
the Radxa ROCK 5B+ (12 GB), the camera is an internal CSI-ribbon module, and the
radio line now reads 915 MHz LoRa + GNSS with a second LoRa radio for relay.
Changed on `/hardware/` (meta description, OG/alt text, hero stat, Power and
Compute spec rows, the "Bring your own power" feature) and on the homepage
(image alt, the "Made for your next question" paragraph).

**Two releases.** `20260930-88a2f1c76264` shipped the copy and the new image,
but Cloudflare kept serving the old bytes at the unchanged
`/images/hardware.jpg` URL (the origin had the new file; the CDN did not
revalidate), so the render was re-cut as a dated asset —
`/images/hardware-20260930.jpg` — with the two page references and the export
verifier pin following it, and shipped as `20260930-9b7aa43cbaea`. The old URL
remains a CDN-cached orphan until its TTL lapses; nothing references it.

Built from commit `9b7aa43`, 64 verified files, tarball sha256
`1a6d734a7801f513401de85e010c051837bd9f1b8acb8409728a685e984cdac1`. Rollback
configuration `/var/www/sage-website/backup-20260930T125312Z` (prior release
`20260930-88a2f1c76264`, and before it the first rollback root
`/var/www/sage-website/backup-20260930T125104Z`). Verified live: `/`,
`/commons/`, `/hardware/` and `/foundation/` all 200; the new image serves
164,628 bytes at its new URL; "Bring your own power" and "Radxa ROCK 5B+" are in
the served HTML; no "10,000 mAh" or "replaceable battery" claim remains.
