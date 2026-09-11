from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NGINX = ROOT / "nginx"


class ProxyArtifactTests(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_sni_routes_preserve_call_tls_and_add_fleet_tls(self) -> None:
        bootstrap = self.text("nginx/stream-conf.d/10-sage-sni-bootstrap.conf")
        final = self.text("nginx/stream-conf.d/10-sage-sni.conf")
        for config in (bootstrap, final):
            self.assertIn("ssl_preread on;", config)
            self.assertIn("call.sage.delivery      127.0.0.1:8444;", config)
            self.assertIn("default                 127.0.0.1:9;", config)
        self.assertIn("fleet-api.sage.delivery 127.0.0.1:9;", bootstrap)
        self.assertIn("fleet-api.sage.delivery 127.0.0.1:9443;", final)

    def test_public_http_only_forwards_to_loopback(self) -> None:
        config = self.text("nginx/conf.d/10-sage-http.conf")
        self.assertIn("server_name call.sage.delivery;", config)
        self.assertIn("proxy_pass http://127.0.0.1:8080;", config)
        self.assertIn("server_name fleet-api.sage.delivery;", config)
        self.assertIn("/.well-known/acme-challenge/", config)
        self.assertNotRegex(config, r"proxy_pass\s+http://(?!127\.0\.0\.1)")

    def test_fleet_tls_route_policy_is_fail_closed(self) -> None:
        config = self.text("nginx/conf.d/20-fleet-tls.conf")
        self.assertIn("listen 127.0.0.1:9443 ssl;", config)
        self.assertIn("ssl_verify_client optional;", config)
        self.assertRegex(
            config,
            r"(?s)location \^~ /v1/device/ \{.*?\$ssl_client_verify != SUCCESS.*?return 403;",
            msg="device routes must reject absent or invalid client certificates",
        )
        enrollment = re.search(
            r"location \^~ /v1/enrollments/ \{(?P<body>.*?)\n    \}", config, re.S
        )
        admin = re.search(r"location \^~ /v1/admin/ \{(?P<body>.*?)\n    \}", config, re.S)
        self.assertIsNotNone(enrollment)
        self.assertIsNotNone(admin)
        self.assertNotIn("ssl_client_verify", enrollment.group("body"))
        self.assertNotIn("ssl_client_verify", admin.group("body"))
        self.assertIn("location / {\n        return 404;", config)

    def test_only_proxy_derived_certificate_headers_reach_device_route(self) -> None:
        config = self.text("nginx/conf.d/20-fleet-tls.conf")
        clear = self.text("nginx/snippets/fleet-clear-client-identity.conf")
        self.assertIn('X-Fleet-Client-Cert-Verified ""', clear)
        self.assertIn('X-Fleet-Client-Cert-Fingerprint ""', clear)
        self.assertIn(
            "X-Fleet-Client-Cert-Verified $ssl_client_verify", config
        )
        self.assertIn(
            "X-Fleet-Client-Cert-Fingerprint $fleet_client_sha256", config
        )
        all_config = "\n".join(
            path.read_text(encoding="utf-8")
            for path in NGINX.rglob("*")
            if path.is_file()
        )
        self.assertNotIn("$ssl_client_fingerprint", all_config)

    def test_njs_hashes_canonical_certificate_der_with_sha256(self) -> None:
        helper = self.text("nginx/njs/fleet_tls.js")
        self.assertIn("ssl_client_raw_cert", helper)
        self.assertIn("Buffer.from(base64Der, 'base64')", helper)
        self.assertIn("createHash('sha256')", helper)
        self.assertIn("return `sha256:", helper)

    def test_service_templates_bind_application_services_to_loopback(self) -> None:
        call = self.text(
            "systemd/sage-call-relay.service.override.conf.template"
        )
        authority = self.text("systemd/sage-fleet-authority.service.override.conf")
        self.assertIn("-http 127.0.0.1:8080", call)
        self.assertIn("-https 127.0.0.1:8444", call)
        proxy = self.text("nginx/snippets/fleet-authority-proxy.conf")
        self.assertIn("proxy_pass http://127.0.0.1:8091", proxy)
        self.assertNotIn("FLEET_HOST", authority)
        self.assertNotIn("FLEET_PORT", authority)
        self.assertIn("IPAddressDeny=any", authority)
        self.assertIn("IPAddressAllow=localhost", authority)

    def test_runbook_preserves_out_of_scope_relay_ports_and_rollback(self) -> None:
        readme = self.text("README.md")
        for required in (
            "TCP/UDP 3478",
            "TCP/UDP 4001",
            "Rollback",
            "nginx -t",
            "Cloudflare orange-cloud",
            "source IP",
        ):
            self.assertIn(required, readme)


if __name__ == "__main__":
    unittest.main()
