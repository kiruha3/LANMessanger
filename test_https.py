"""HTTPS must work with portable CA roots, preserve hostname checks and reject impostors."""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.core.https import https_context, open_url, network_error_message


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'verified')


class HTTPSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        now = datetime.now(timezone.utc)
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'LANMessenger test CA')])
        ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
              .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(ca_key, hashes.SHA256()))
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')]))
                .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False)
                .sign(ca_key, hashes.SHA256()))
        cls.ca = root/'roots.pem'; cls.ca.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        cert = root/'leaf.pem'; cert.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
        private = root/'key.pem'; private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,private)
        cls.server.socket=ctx.wrap_socket(cls.server.socket,server_side=True)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(2);cls.temp.cleanup()

    def test_bundled_ca_works_without_system_roots(self):
        with patch('app.core.https.certifi.where',return_value=str(self.ca)), patch('app.core.https.sys.platform','linux'), patch.object(ssl.SSLContext,'load_default_certs'):
            ctx=https_context()
            self.assertEqual(ctx.verify_mode,ssl.CERT_REQUIRED)
            self.assertTrue(ctx.check_hostname)
            with open_url(f'https://localhost:{self.server.server_port}',timeout=3) as response:
                self.assertEqual(response.read(),b'verified')

    def test_native_context_accepts_explicit_roots(self):
        with patch('app.core.https.certifi.where',return_value=str(self.ca)):
            with open_url(f'https://localhost:{self.server.server_port}',timeout=3) as response:
                self.assertEqual(response.read(),b'verified')

    def test_unknown_ca_and_wrong_hostname_remain_rejected(self):
        from urllib.error import URLError
        with self.assertRaises(URLError):
            open_url(f'https://localhost:{self.server.server_port}',timeout=3)
        with patch('app.core.https.certifi.where',return_value=str(self.ca)):
            with self.assertRaises(URLError):
                open_url(f'https://127.0.0.1:{self.server.server_port}',timeout=3)

    def test_certificate_error_has_actionable_message(self):
        from urllib.error import URLError
        text=network_error_message(URLError(ssl.SSLCertVerificationError('issuer missing')))
        self.assertIn('сертификат',text)
        self.assertIn('дату',text)
        self.assertEqual(network_error_message(ValueError('keep this')), 'keep this')


if __name__=='__main__':unittest.main()
