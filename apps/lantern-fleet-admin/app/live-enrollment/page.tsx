import Link from 'next/link';
import { requireChatGPTUser } from '@/app/chatgpt-auth';
import LiveEnrollmentConsole from './live-enrollment-console';
import styles from './page.module.css';

export const dynamic = 'force-dynamic';

export default async function LiveEnrollmentPage() {
  const user = await requireChatGPTUser('/live-enrollment');
  return (
    <main className={styles.shell}>
      <header className={styles.header}>
        <div>
          <span className={styles.eyebrow}>SAGE Lantern Fleet</span>
          <h1>Live device enrollment</h1>
          <p>Physical-possession pairing against the dedicated Fleet Authority.</p>
        </div>
        <div className={styles.headerActions}>
          <span>Signed in as {user.displayName}</span>
          <Link href="/">Fixture workspace</Link>
        </div>
      </header>
      <LiveEnrollmentConsole />
    </main>
  );
}
