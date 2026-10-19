# Which weights are load-bearing

The site loads both families **variable** (`next/font/google`, latin subset, no
weight list), so a headline can sit at 450 and the wordmark at 650 without
either being a separate file. If the film wants fixed cuts instead, these are
the weights the site actually uses:

* **Geist (sans)** — body copy 400 (16px); headings 450 (hero clamp 76–128px,
  section headings 38–64px) with tight negative tracking (−2.5 to −7.5px);
  **wordmark 650** (35px, −2px tracking); UI labels 500; buttons 550; stat
  figures 450. The band that matters is **400–650**, and 450/650 are the two
  that carry the identity.
* **Geist Mono** — eyebrows and small labels only, **400–500**, at 9–12.5px with
  0.4–1.4px letter-spacing, uppercase for eyebrows. It is never used for body
  copy.

The italic serif accent (`.serif` in `globals.css`) is currently Georgia, which
cannot be embedded — the owner owes a decision between Source Serif 4,
Newsreader, EB Garamond and Libre Baskerville. When he picks one, the site gets
the same face so the accent stays identical between screen and social.
