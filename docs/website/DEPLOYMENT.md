# Deployment validation — 9 September 2026

Host: existing SAGE Natter origin, identified from the SAGE v11.5 deployment plan and pinned SSH host key.
Public hostname: sage.ngo, as explicitly reaffirmed by the owner. An initial local resolver gave an empty A answer; Cloudflare (1.1.1.1) and Google (8.8.8.8) both confirmed configured proxied A/AAAA records. No DNS mutations made.

Production export passes. Full TypeScript check passes. Authored-source lint passes. Static HTML check verifies one H1, all internal section anchors, all local asset references, email contact, and illustration labels. npm audit reports zero vulnerabilities after compatible framework updates and a patched sharp override. The unused upstream shadcn catalog has existing lint diagnostics and is outside the authored-source lint target. No browser interaction/visual test was requested or claimed.

A certificate was initially prepared for natter.sage.delivery but no website release was activated there. The user reaffirmed sage.ngo and the certificate route was retargeted to that domain. Site uses a dedicated 127.0.0.1:9444 HTTPS listener behind the existing nginx SNI router. Existing call relay, Natter and Fleet routes are retained.

Final release: `20260909-638366e018ff`, 39 verified files. Activated on sage.ngo with nginx validation and configuration rollback backup. Added the rural family connectivity concept to the homepage; MeshCore and planned Starlink messaging appear on both homepage and hardware page. All exported images use static URLs. Original Natter service routes remain in place.

Pitch deck companion: `docs/SAGE_Foundation_Updated_September_2026.pptx` and `.pdf`. The 26-slide edition uses Geist/Georgia, the navy/amber website palette, selected Commons mark and current concept imagery. Added hardware slide 5 and strengthened network slide 21. Original September deck preserved. Same copywriting skill applied across visible slide copy. Speculative radio modifications are not added to the public promise. PPTX package, geometry, fonts and reimport passed; all slide renders inspected. PDF is a fixed visual sharing copy; PPTX retains editable text and diagrams.
