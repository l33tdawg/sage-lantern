/**
 * `next/image` stand-in for the disconnected preview.
 *
 * The real loader is a framework concern (it wants an optimizer endpoint), and
 * the preview must not open one. The attached-photo path in `screen.tsx` only
 * needs an `<img>` at a known size, so that is exactly what this is — no
 * optimization, no network, no behaviour change to any other screen.
 */
export default function Image({ src, alt, width, height }: {
  src: string; alt: string; width?: number; height?: number; unoptimized?: boolean;
}) {
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt={alt} width={width} height={height} />;
}
