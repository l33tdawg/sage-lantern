/**
 * Renders an email address that Cloudflare's Email Address Obfuscation must not
 * rewrite.
 *
 * The site is a static export, so the HTML is final before Cloudflare sees it.
 * With obfuscation on, Cloudflare replaces each address in the served HTML with
 * a `<span class="__cf_email__" data-cfemail="…">[email protected]</span>` — the
 * text a visitor reads is no longer the text React rendered, so hydration fails
 * with error #418 ("server rendered text did not match the client").
 *
 * Cloudflare honours the documented `email_off` comment pair and leaves
 * everything between the markers untouched. React has no way to emit a comment
 * node, so the address is written as raw markup: the same string on the server
 * and in the client, which is what hydration compares.
 *
 * Use this for the address *text*. An obfuscated `mailto:` href is harmless by
 * comparison — it is an attribute, not text, and Cloudflare's decode script
 * restores it in the browser.
 */
export function EmailOff({ address }: { address: string }) {
  return (
    <span
      className="email-off"
      dangerouslySetInnerHTML={{ __html: `<!--email_off-->${address}<!--/email_off-->` }}
    />
  );
}
