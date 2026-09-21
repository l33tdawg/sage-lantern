import type { Metadata } from 'next';

export function pageMetadata(title: string, description: string, path: string, image: string, alt: string): Metadata {
  const url = `https://sage.ngo${path}`;
  const images = [{ url: `https://sage.ngo/images/${image}`, width: 1536, height: 1024, type: 'image/jpeg', alt }];
  return {
    title,
    description,
    alternates: { canonical: url },
    openGraph: { type: 'website', locale: 'en_GB', siteName: 'SAGE Foundation', url, title, description, images },
    twitter: { card: 'summary_large_image', title, description, images },
  };
}
