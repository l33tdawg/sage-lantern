# Public dependency examples in release scanning

2026-09-06. These are publicly distributed development/example keys, not Lantern
identities. Never use them for a device vault, SAGE signing or production TLS.

The manifest scanner retains private-key rejection, including escaped newline
literals. Two exceptions require both the exact dependency-relative path and the
complete reviewed file SHA256. A changed/appended byte with a matching key marker
fails; another path fails. Symlink, hardlink, mode and source-change checks remain.
This does not allow a directory, package version or arbitrary public-looking key.

## Reviewed upstream evidence

- Miniflare 4.20260515.0, `node_modules/miniflare/dist/src/index.js`:
  file SHA256 `9584409d464f6c21d720b20da3c5dc6e5bdab0393f9cb19b36cb960beaf0958b`.
  Its one EC example block matches the concatenated literals in
  [Cloudflare's tagged cert.ts](https://github.com/cloudflare/workers-sdk/blob/a3fa623f2abf192e57d876c727bfa107aa297ec9/packages/miniflare/src/http/cert.ts).
  Source-file SHA256 `1a7e273ecf8d656c1b90ad6b5c20603303285f0074a3dadbab4509ec540865d6`;
  normalized block SHA256 `3dd311499b864c12562634af0a469372d46ab2027d2024eeee8177ae20e46157`.
- Wrangler 4.92.0, `node_modules/wrangler/wrangler-dist/cli.js`:
  file SHA256 `fc1aa72afc91906759a3555e76e6dae1b13504f71dec6d3bbfa0fe24c351ebd7`.
  Three escaped RSA example blocks match public comment examples distributed in
  [Cloudflare SDK 5.2.0](https://registry.npmjs.org/cloudflare/-/cloudflare-5.2.0.tgz).
  Each normalized block SHA256 is
  `5269a1238ad7788bef115210f06702ab6e0d18d60efdef5ba0a9a034c2217ad8`.

Curie's read-only provenance review matched both installed files to published
npm tarballs and checked tarball integrity against the package lock. Normalized
block hashes exclude the newline following the END delimiter. Parent verified
the actual file hashes through the revised scanner. No key bytes are reproduced.

The complete 31,128-file ARM64 UI tree passes scanning with these exact pins.
Focused manifest/stager tests pass 32 tests and 114 subtests, including altered
key body, appended bytes/second key, wrong path and escaped PEM rejection.
This is packaging evidence only, not device authentication or TLS acceptance.
