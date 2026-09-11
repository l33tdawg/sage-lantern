import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  metadataBase: new URL(process.env.SITE_ORIGIN ?? 'http://localhost:3000'),
  title: 'SAGE Lantern Fleet Admin',
  description: 'Identity-safe operations, provisioning, evidence, and release management for SAGE Lantern fleets.',
  openGraph: {
    title: 'SAGE Lantern Fleet Admin',
    description: 'Identity-safe operations for every Lantern.',
    images: [{ url: '/og.png', width: 1731, height: 909, alt: 'SAGE Lantern Fleet Admin connected lantern network' }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'SAGE Lantern Fleet Admin',
    description: 'Identity-safe operations for every Lantern.',
    images: ['/og.png'],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
