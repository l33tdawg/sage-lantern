'use client';

import { FormEvent, useCallback, useEffect, useState } from 'react';
import styles from './page.module.css';
import { parseCreatedEnrollment, parseEnrollmentList, parseEnrollmentSession, type CreatedEnrollment, type Enrollment, type Session } from '../../lib/enrollment-responses';

export default function LiveEnrollmentConsole() {
  const [session, setSession] = useState<Session | null>(null);
  const [enrollments, setEnrollments] = useState<Enrollment[]>([]);
  const [created, setCreated] = useState<CreatedEnrollment | null>(null);
  const [deviceId, setDeviceId] = useState('lantern-a');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    const sessionResponse = await fetch('/api/whoami', { cache: 'no-store' });
    const sessionBody = await sessionResponse.json();
    if (!sessionResponse.ok) throw new Error(apiError(sessionBody));
    const current = parseEnrollmentSession(sessionBody);
    setSession(current);
    if (!current.capabilities.read) return;

    const enrollmentResponse = await fetch('/api/enrollments', { cache: 'no-store' });
    const enrollmentBody = await enrollmentResponse.json();
    if (!enrollmentResponse.ok) throw new Error(apiError(enrollmentBody));
    setEnrollments(parseEnrollmentList(enrollmentBody));
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      refresh().catch((cause) => setError(message(cause)));
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  async function createEnrollment(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const response = await fetch('/api/enrollments', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: deviceId, expires_in_seconds: 900 }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(apiError(body));
      setCreated(parseCreatedEnrollment(body));
      await refresh();
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }

  async function approveEnrollment(enrollment: Enrollment) {
    if (!enrollment.comparison_code) return;
    setBusy(true);
    setError('');
    try {
      const response = await fetch(
        `/api/enrollments/${encodeURIComponent(enrollment.enrollment_id)}/approve`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ comparison_code: enrollment.comparison_code }),
        },
      );
      const body = await response.json();
      if (!response.ok) throw new Error(apiError(body));
      await refresh();
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }

  if (!session && !error) return <section className={styles.card}>Loading authority assignment…</section>;

  return (
    <div className={styles.grid}>
      <section className={styles.card}>
        <span className={styles.eyebrow}>Server-resolved authority</span>
        <h2>{session?.role ?? 'No fleet role assigned'}</h2>
        <p>
          The preview role selector cannot authorize this page. Access comes from an
          exact, server-side user assignment with scope and optional expiry.
        </p>
        {session && !session.capabilities.read && (
          <div className={styles.warning}>
            Give the administrator this user ID to create the first assignment:
            <code>{session.user.userId}</code>
          </div>
        )}
        {error && <div className={styles.error}>{error}</div>}
      </section>

      <section className={styles.card}>
        <span className={styles.eyebrow}>Single-use setup</span>
        <h2>Create enrollment</h2>
        <form onSubmit={createEnrollment} className={styles.form}>
          <label>
            Device ID
            <input
              value={deviceId}
              onChange={(event) => setDeviceId(event.target.value)}
              pattern="[a-zA-Z0-9][a-zA-Z0-9._-]{2,63}"
              required
              disabled={!session?.capabilities.createEnrollment || busy}
            />
          </label>
          <button disabled={!session?.capabilities.createEnrollment || busy}>
            Create 15-minute setup secret
          </button>
        </form>
        {created && (
          <div className={styles.secret}>
            <strong>Copy now—this secret is shown once</strong>
            <code>{created.setup_secret}</code>
            <small>Enrollment: {created.enrollment_id}</small>
          </div>
        )}
      </section>

      <section className={`${styles.card} ${styles.wide}`}>
        <div className={styles.sectionHeading}>
          <div>
            <span className={styles.eyebrow}>Physical-possession queue</span>
            <h2>Enrollments</h2>
          </div>
          <button className={styles.secondary} onClick={() => refresh().catch((cause) => setError(message(cause)))} disabled={!session?.capabilities.read || busy}>
            Refresh
          </button>
        </div>
        {!session?.capabilities.read ? (
          <p>Enrollment records stay hidden until a fleet role is assigned.</p>
        ) : enrollments.length === 0 ? (
          <p>No live enrollment records.</p>
        ) : (
          <div className={styles.list}>
            {enrollments.map((enrollment) => (
              <article key={enrollment.enrollment_id}>
                <div>
                  <strong>{enrollment.device_id}</strong>
                  <small>{enrollment.enrollment_id}</small>
                </div>
                <span className={styles.status}>{enrollment.status}</span>
                <div>
                  <small>Comparison code</small>
                  <code>{enrollment.comparison_code ?? 'Waiting for device claim'}</code>
                </div>
                <button
                  disabled={
                    busy ||
                    enrollment.status !== 'claimed' ||
                    !enrollment.comparison_code ||
                    !session.capabilities.approveEnrollment
                  }
                  onClick={() => approveEnrollment(enrollment)}
                >
                  Confirm matching code
                </button>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function apiError(value: unknown): string {
  if (!value || typeof value !== 'object') return 'Request failed';
  const body = value as Record<string, unknown>;
  if (typeof body.error === 'string') return body.error;
  return 'Request failed';
}

function message(cause: unknown): string {
  return cause instanceof Error ? cause.message : 'Request failed';
}
