import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


def load(name,relative):
    spec=importlib.util.spec_from_file_location(name,ROOT/relative)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


console=load('public_console','infrastructure/monitoring/console.py')
wake=load('public_wake','tools/wake.py')
settings=load('public_sso','infrastructure/monitoring/sso-server-config.py')


class PublicBoundaries(unittest.TestCase):
    def test_actions_remain_opt_in_with_fresh_telemetry(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(console,'local_config',return_value={}),patch.object(console.subprocess,'Popen') as process:
            monitor=console.Monitor(False,Path(folder))
            monitor.current={'connection':'online','observed':__import__('time').time(),'power_version':'2','sessions_version':'2'}
            with self.assertRaisesRegex(ValueError,'disabled by default'):
                monitor.action('windows-shutdown','ARRETER CE PC WINDOWS')
            process.assert_not_called()

    def test_lan_packet_requires_a_private_local_source_in_its_subnet(self):
        config={'mac':'02:00:00:00:00:01','source':'192.168.50.10','network':'192.168.50.0/24'}
        source,broadcast,payload=wake.validate(config)
        self.assertEqual(str(broadcast),'192.168.50.255')
        self.assertEqual(len(payload),102)
        for bad in ['8.8.8.8','100.64.1.2','192.168.51.10']:
            with self.assertRaises(ValueError):wake.validate({**config,'source':bad})

    def test_sso_rejects_http_credentials_in_url_and_unknown_paths(self):
        for origin in ['http://admin.example.org','https://user@admin.example.org','https://admin.example.org/extra','https://localhost']:
            with self.assertRaises(ValueError):settings.hostname(origin)
        self.assertEqual(settings.hostname('https://admin.example.org'),'admin.example.org')
