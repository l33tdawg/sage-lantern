/**
 * A stand-in for `next/image`.
 *
 * The real component asks the framework's optimizer for a `/_next/image` URL,
 * which needs a server that understands it. The device screens only ever render
 * a local data URL or a same-origin blob, so the review substitutes a plain
 * `<img>` that keeps the caller's width, height and alt. This changes how the
 * bytes are painted, not which bytes were chosen — the source that decides that
 * is the app's own, and it is bundled unmodified.
 */
export default function Image({ src, alt, width, height, unoptimized, ...rest }: {
  src: string; alt: string; width?: number; height?: number; unoptimized?: boolean;
} & Record<string, unknown>) {
  void unoptimized;
  return <img src={src} alt={alt} width={width} height={height} {...rest} />;
}
