import Dashboard from './dashboard';
import { requireChatGPTUser } from './chatgpt-auth';
import { redirect } from 'next/navigation';

export const dynamic = 'force-dynamic';

export default async function Home() {
  if (process.env.LANTERN_EDGE_LOCAL === '1') {
    redirect('/device');
  }

  const user = await requireChatGPTUser('/');
  return <Dashboard displayName={user.fullName ?? user.email.split('@')[0]} />;
}
