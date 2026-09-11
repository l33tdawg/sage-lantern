import { notFound } from 'next/navigation';
import NetworkSetup from './setup';

export const dynamic = 'force-dynamic';
export const metadata = { title: 'Set up your Lantern' };

export default function SetupPage() {
  if (process.env.LANTERN_EDGE_LOCAL !== '1') notFound();
  return <NetworkSetup />;
}
