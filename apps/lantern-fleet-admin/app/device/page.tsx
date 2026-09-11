import { notFound } from 'next/navigation';
import DeviceAccessBoundary from './access';

export const dynamic = 'force-dynamic';
export const metadata = { title: 'SAGE Lantern', description: 'Private, on-device voice assistance.', openGraph: { images: [] }, twitter: { images: [] } };

export default function DevicePage() {
  if (process.env.LANTERN_EDGE_LOCAL !== '1') notFound();
  return <DeviceAccessBoundary enabled={process.env.LANTERN_DEVICE_LOCK === '1'} />;
}
