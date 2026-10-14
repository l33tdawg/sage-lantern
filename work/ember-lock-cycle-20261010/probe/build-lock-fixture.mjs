/**
 * Build the lock-cycle preview of the real access boundary.
 *
 * Bundles `apps/lantern-fleet-admin/app/device/access.tsx` (and through it the
 * real `screen.tsx`) with esbuild, aliasing only `next/image` to a local
 * stand-in so no framework optimizer is needed. Writes the HTML shell beside
 * the bundle so the directory is directly servable over HTTP — the review
 * browser refuses `file:` URLs.
 *
 *   node probe/build-lock-fixture.mjs
 *
 * Nothing under `apps/` is written by this script; it only reads.
 */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { writeFileSync, readFileSync } from 'node:fs';

const here = dirname(fileURLToPath(import.meta.url));
const app = join(here, '../../../apps/lantern-fleet-admin');
const require = createRequire(join(app, 'package.json'));
const { build } = require('esbuild');

const result = await build({
  entryPoints: [join(here, 'lock-fixture-entry.tsx')],
  outfile: join(here, 'lock-fixture.js'),
  bundle: true,
  platform: 'browser',
  format: 'iife',
  jsx: 'automatic',
  nodePaths: [join(app, 'node_modules')],
  metafile: true,
  logLevel: 'warning',
  // The app's styles and this frame's styles are injected by the plugin below
  // and applied at runtime. The device screens are styled entirely by their own
  // CSS (`.device-screen button` is what gives every control its 72px target),
  // so losing the sheet does not merely look wrong — it silently changes the
  // geometry a scenario measures.
  plugins: [{
    // `loader: { '.css': 'js' }` is a later esbuild API than the one pinned in
    // this tree, and `'css'` would emit a sibling stylesheet that an IIFE page
    // never links. This does the same job with the API that exists here.
    name: 'inject-css',
    setup(builder) {
      builder.onLoad({ filter: /\.css$/ }, (args) => ({
        contents: `(() => {
          const tag = document.createElement('style');
          tag.dataset.source = ${JSON.stringify(args.path.split('/').slice(-1)[0])};
          tag.textContent = ${JSON.stringify(readFileSync(args.path, 'utf8'))};
          document.head.appendChild(tag);
        })();`,
        loader: 'js',
      }));
    },
  }, {
    // One React, from the app that owns the component. A second copy resolves as
    // "Invalid hook call" and would make the whole preview worthless.
    name: 'single-react',
    setup(builder) {
      builder.onResolve({ filter: /^react(-dom)?(\/.*)?$/ }, (args) => ({
        path: require.resolve(args.path, { paths: [app] }),
      }));
      builder.onResolve({ filter: /^next\/image$/ }, () => ({
        path: join(here, 'next-image-stand-in.tsx'),
      }));
    },
  }],
});

writeFileSync(join(here, 'lock-fixture.html'), `<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ember — lock cycle through the real access boundary</title>
<div id="root"></div>
<script src="lock-fixture.js"></script>
`);
writeFileSync(join(here, 'lock-build-metafile.json'), `${JSON.stringify(result.metafile, null, 2)}\n`);

const inputs = Object.keys(result.metafile.inputs).filter((path) => !path.includes('node_modules'));
console.log('Lock preview built. Source inputs:');
for (const path of inputs.sort()) console.log(`  ${path}`);
const reactCopies = new Set(Object.keys(result.metafile.inputs)
  .filter((path) => /node_modules\/react(-dom)?\//.test(path))
  .map((path) => path.split('node_modules/').at(-1).split('/')[0]));
console.log(`React packages bundled: ${[...reactCopies].sort().join(', ')}`);
