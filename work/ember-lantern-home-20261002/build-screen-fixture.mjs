/**
 * Build the integrated Screen preview.
 *
 * Bundles the real `screen.tsx` (and through it the real Home, capture owner,
 * status pipeline and New chat reset) with esbuild, aliasing only `next/image`
 * to a local stand-in so no framework optimizer is needed. Writes the HTML
 * shell beside the bundle so the evidence directory is directly servable over
 * HTTP — the review browser refuses `file:` URLs.
 *
 *   node build-screen-fixture.mjs
 */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { writeFileSync } from 'node:fs';

const here = dirname(fileURLToPath(import.meta.url));
const app = join(here, '../../apps/lantern-fleet-admin');
const require = createRequire(join(app, 'package.json'));
const { build } = require('esbuild');

const result = await build({
  entryPoints: [join(here, 'screen-fixture-entry.tsx')],
  outfile: join(here, 'screen-fixture.js'),
  bundle: true,
  platform: 'browser',
  format: 'iife',
  jsx: 'automatic',
  nodePaths: [join(app, 'node_modules')],
  metafile: true,
  logLevel: 'warning',
  loader: { '.css': 'css' },
  plugins: [{
    // One React, from the app that owns the component. A second copy resolves as
    // "Invalid hook call" and would make the whole preview worthless.
    name: 'single-react',
    setup(builder) {
      builder.onResolve({ filter: /^react(-dom)?(\/.*)?$/ }, (args) => ({
        path: require.resolve(args.path, { paths: [app] }),
      }));
      builder.onResolve({ filter: /^next\/image$/ }, () => ({
        path: join(here, 'screen-fixture-next-image.tsx'),
      }));
    },
  }],
});

writeFileSync(join(here, 'screen-fixture.html'), `<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ember — integrated Screen preview (Home 2a)</title>
<link rel="stylesheet" href="screen-fixture.css">
<div id="root"></div>
<script src="screen-fixture.js"></script>
`);
writeFileSync(join(here, 'screen-build-metafile.json'), `${JSON.stringify(result.metafile, null, 2)}\n`);

const inputs = Object.keys(result.metafile.inputs).filter((path) => !path.includes('node_modules'));
console.log('Screen preview built. Source inputs:');
for (const path of inputs.sort()) console.log(`  ${path}`);
const reactCopies = new Set(Object.keys(result.metafile.inputs)
  .filter((path) => /node_modules\/react(-dom)?\//.test(path))
  .map((path) => path.split('node_modules/').at(-1).split('/')[0]));
console.log(`React packages bundled: ${[...reactCopies].sort().join(', ')}`);
