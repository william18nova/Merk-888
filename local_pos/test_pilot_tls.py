import json
import os
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import patch
from .pilot import make_certificate
from .pilot_tls import FORMAT, private_host, validate_link, context_for
from .runtime import save_json


class PilotTlsTests(unittest.TestCase):
    def test_private_ipv4_only(self):
        for host in ('127.0.0.1','192.168.1.2','10.0.0.1','172.16.0.2'):
            self.assertEqual(private_host(host),host)
        for host in ('8.8.8.8','0.0.0.0','169.254.0.1','::1','example.com','224.1.2.3'):
            with self.assertRaises(ValueError): private_host(host)

    def test_no_pilot_does_not_override_normal_tls(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertIsNone(context_for('https://example.com'))

    def test_pinned_ca_requires_exact_private_server_and_valid_certificate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            certificate,digest=make_certificate(root,'127.0.0.1')
            data={'format':FORMAT,'url':'https://127.0.0.1:8940','certificate':certificate,'sha256':digest}
            self.assertEqual(validate_link(data),data['url'])
            save_json(root/'pilot-trust.json',data)
            with patch.dict(os.environ,{'NOVA_LOCAL_CONFIG':str(root/'local.json')}):
                context=context_for(data['url'])
                self.assertTrue(context.check_hostname)
                self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
                for url in ('http://127.0.0.1:8940','https://127.0.0.1:8941','https://example.com'):
                    with self.assertRaises(ValueError): context_for(url)
            for values in ({'sha256':'0'*64},{'url':'https://8.8.8.8:8940'},{'extra':True}):
                with self.assertRaises(ValueError): validate_link({**data,**values})

    def test_invalid_trust_file_does_not_fall_back_to_insecure_tls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            save_json(root/'pilot-trust.json',{})
            with patch.dict(os.environ,{'NOVA_LOCAL_CONFIG':str(root/'local.json')}):
                with self.assertRaises(ValueError): context_for('https://127.0.0.1:8940')
