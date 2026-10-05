import tempfile
import unittest
from pathlib import Path

from protos import celaut_pb2
from src import config
from src.gateway import GatewayError, address_of, gateway_address


class EnvsTest(unittest.TestCase):

    def test_defaults(self):
        self.assertEqual(config.parse_envs({}), config.DEFAULT_ENVS)

    def test_values_and_types(self):
        envs = config.parse_envs({'SAVE_TRAIN_DATA': b'3', 'SOLVE_TIMEOUT': ' 7 '})
        self.assertEqual(envs['SAVE_TRAIN_DATA'], 3)
        self.assertEqual(envs['SOLVE_TIMEOUT'], 7)

    def test_bad_values_keep_the_default(self):
        messages = []
        envs = config.parse_envs({'SAVE_TRAIN_DATA': 'x', 'MAX_WORKERS': '-1'}, log=messages.append)
        self.assertEqual(envs['SAVE_TRAIN_DATA'], config.DEFAULT_ENVS['SAVE_TRAIN_DATA'])
        self.assertEqual(envs['MAX_WORKERS'], config.DEFAULT_ENVS['MAX_WORKERS'])
        self.assertEqual(len(messages), 2)

    def test_config_file_has_priority(self):
        configuration_file = celaut_pb2.ConfigurationFile()
        configuration_file.config.environment_variables.add(key='MAX_WORKERS', value=b'4')
        envs = config.load_envs(configuration_file, environ={'MAX_WORKERS': '9', 'SOLVE_TIMEOUT': '11'})
        self.assertEqual(envs['MAX_WORKERS'], 4)
        self.assertEqual(envs['SOLVE_TIMEOUT'], 11)


class DependenciesTest(unittest.TestCase):

    def test_read_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".dependencies"
            path.write_text("REGRESSION=" + "a" * 64 + "\n# comment\n\nBAD\nEMPTY=\nRANDOM = " + "b" * 64 + "\n")
            self.assertEqual(config.read_dependencies(path), {"REGRESSION": "a" * 64, "RANDOM": "b" * 64})
            self.assertEqual(config.read_dependencies(Path(tmp) / "missing"), {})

    def test_is_service_id(self):
        self.assertTrue(config.is_service_id("0" * 64))
        self.assertFalse(config.is_service_id("A" * 64))
        self.assertFalse(config.is_service_id("../" + "0" * 61))
        self.assertFalse(config.is_service_id("0" * 63))
        self.assertFalse(config.is_service_id(None))


class AddressTest(unittest.TestCase):

    def test_gateway_address(self):
        configuration_file = celaut_pb2.ConfigurationFile()
        slot = configuration_file.gateway.uri_slot.add(internal_port=8090)
        slot.uri.add(ip="192.168.200.1", port=58614)
        self.assertEqual(gateway_address(configuration_file), "192.168.200.1:58614")

    def test_address_of_port(self):
        instance = celaut_pb2.Instance()
        instance.uri_slot.add(internal_port=1).uri.add(ip="10.0.0.1", port=1000)
        instance.uri_slot.add(internal_port=2).uri.add(ip="10.0.0.2", port=2000)
        self.assertEqual(address_of(instance), "10.0.0.1:1000")
        self.assertEqual(address_of(instance, port=2), "10.0.0.2:2000")

    def test_no_address(self):
        instance = celaut_pb2.Instance()
        instance.uri_slot.add(internal_port=1)
        with self.assertRaises(GatewayError):
            address_of(instance)


if __name__ == "__main__":
    unittest.main()
