import { notFound } from 'next/navigation';
import LanternLocalConsole from '../lantern-local-console';

export const dynamic = 'force-dynamic';
export const metadata = { title: 'Lantern administration' };

export default async function AdminPage() {
  if (process.env.LANTERN_EDGE_LOCAL === '1') {
    return <LanternLocalConsole displayName={process.env.LANTERN_EDGE_OPERATOR ?? 'Local operator'} />;
  }
  notFound();
}
