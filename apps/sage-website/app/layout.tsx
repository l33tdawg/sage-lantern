import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import './globals.css';
import { pageMetadata } from '@/lib/metadata';

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
});

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
});

export const metadata: Metadata = {
  metadataBase: new URL('https://sage.ngo'),
  ...pageMetadata(
    'SAGE Lantern — Intelligence. For all of us.',
    'A curious mind shouldn’t need a better postcode. We’re building local AI and community networks so more people can learn, create and stay connected.',
    '/',
    'hero.jpg',
    'A child and two adults exploring a SAGE Lantern together — AI-generated concept illustration.',
  ),
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
