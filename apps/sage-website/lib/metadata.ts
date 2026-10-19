import type { Metadata } from 'next';

// The primary domain. sage.ngo and sage-commons.org are secondary: they serve
// the same pages but declare this host as the canonical one.
export const PRIMARY_ORIGIN = 'https://sage-foundation.net';

export function pageMetadata(title: string, description: string, path: string, image: string, alt: string): Metadata {
  const url = `${PRIMARY_ORIGIN}${path}`;
  const images = [{ url: `${PRIMARY_ORIGIN}/images/${image}`, width: 1536, height: 1024, type: 'image/jpeg', alt }];
  return {
    title,
    description,
    alternates: { canonical: url },
    openGraph: { type: 'website', locale: 'en_GB', siteName: 'SAGE Foundation', url, title, description, images },
    twitter: { card: 'summary_large_image', title, description, images },
  };
}
