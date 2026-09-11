# SAGE public website

SAGE Foundation initiative homepage. React 19, Vinext/Vite, Tailwind 4, shadcn/Base UI. Static export hosted alongside existing services on the owner's SAGE Natter server.

- `npm ci` — install exact lockfile.
- `npm run dev` — local preview.
- `npm run build` — export public files to `dist/client`.
- `npm run lint` — check project-authored code (vendored starter catalog retains its upstream implementation).
- `npx tsc --noEmit` — typecheck the entire project.

Brand: owner-selected 03 / Commons geometric lantern. Exact device accent `#ffbe62`, background `#0b1820`, surface `#172a34`. The device app itself was not modified.

Public contact: dhillon@sage.ngo. Contact cards open email drafts; the website does not send messages or collect form data. No payment collection, analytics, authentication or private device-service access. Both page images are labelled AI illustrations.

## Deployment

Only `dist/client` is web content. Never upload the workspace, device evidence, keys or source caches. `deploy/30-sage-website.conf` terminates website TLS on a dedicated loopback listener, routed by the existing SNI multiplexer. `deploy/install-release.py` validates a bounded change, backs up configuration and activates a release symlink; failures restore the old configuration.

Production hostname: https://sage.ngo/. The server is the existing SAGE Natter origin. Public DNS was confirmed through Cloudflare and Google resolvers after a stale local negative DNS answer. No DNS or public-domain change was made.

Copy and skill provenance is kept in the private `internal-docs/website/` tree,
outside this repository.
