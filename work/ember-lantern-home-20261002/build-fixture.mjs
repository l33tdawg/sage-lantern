/**
 * Build the disconnected Home fixture.
 *
 * Bundles the real submitted component and its real helpers with esbuild, then
 * writes the HTML shell. No dependency installation and no device transport.
 *
 *   node build-fixture.mjs
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
  entryPoints: [join(here, 'fixture-entry.tsx')],
  outfile: join(here, 'home-fixture.js'),
  bundle: true,
  platform: 'browser',
  jsx: 'automatic',
  nodePaths: [join(app, 'node_modules')],
  metafile: true,
  logLevel: 'warning',
  plugins: [{
    // One React, from the app that owns the component. Without this the fixture
    // entry resolves `react` by walking up from this directory and can land on a
    // different copy than `home.tsx` does, which renders as "Invalid hook call"
    // and would make the fixture evidence worthless.
    name: 'single-react',
    setup(builder) {
      builder.onResolve({ filter: /^react(-dom)?(\/.*)?$/ }, (args) => ({
        path: require.resolve(args.path, { paths: [app] }),
      }));
    },
  }],
});

writeFileSync(join(here, 'home-fixture.html'), `<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ember — Home 2a disconnected fixture</title>
<link rel="stylesheet" href="home-fixture.css">
<div id="root"></div>
<script src="home-fixture.js"></script>
`);
// The bundle inputs are the evidence that this is the reviewed source and not a
// copy: print them so the bundle can be tied back to exact file hashes.
writeFileSync(join(here, 'build-metafile.json'), `${JSON.stringify(result.metafile, null, 2)}\n`);
const inputs = Object.keys(result.metafile.inputs).filter((path) => !path.includes('node_modules'));
console.log('Fixture built. Source inputs:');
for (const path of inputs.sort()) console.log(`  ${path}`);
const reactCopies = new Set(Object.keys(result.metafile.inputs)
  .filter((path) => /node_modules\/react(-dom)?\//.test(path))
  .map((path) => path.split('node_modules/').at(-1).split('/')[0]));
console.log(`React packages bundled: ${[...reactCopies].sort().join(', ')}`);
