"""Explicit HTTPS trust for portable downloads; LAN TLS contexts stay independent."""
import ssl
import sys
from urllib.error import URLError
import urllib.request

import certifi


def https_context():
    if sys.platform == 'win32':
        # CryptoAPI builds certificate chains using Windows roots/intermediates.
        # No global ssl injection: peer TLS uses its own fingerprint policy.
        import truststore
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    else:
        context = ssl.create_default_context()
    # Also work on a fresh machine whose system CA cache is incomplete.
    context.load_verify_locations(cafile=certifi.where())
    return context


def open_url(request, *, timeout=10):
    return urllib.request.urlopen(request, timeout=timeout, context=https_context())


def network_error_message(error):
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        return ('Не удалось подтвердить HTTPS-сертификат сервера. Проверьте дату и время Windows. '
                'Если сеть или антивирус проверяет HTTPS, его корневой сертификат должен быть '
                'доверенным в Windows. После исправления повторите установку компонентов ИИ.')
    return str(error)
