import crypto from 'crypto';

function clientFingerprint(r) {
    const pem = r.variables.ssl_client_raw_cert;
    if (!pem) {
        return '';
    }

    const base64Der = pem
        .replace('-----BEGIN CERTIFICATE-----', '')
        .replace('-----END CERTIFICATE-----', '')
        .replace(/\s/g, '');
    const der = Buffer.from(base64Der, 'base64');
    return `sha256:${crypto.createHash('sha256').update(der).digest('hex')}`;
}

export default {clientFingerprint};
