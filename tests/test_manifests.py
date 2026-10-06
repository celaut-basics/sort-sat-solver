"""Static checks of the service manifests against the rules of nodo dev.

- docs/PACKING.md and src/packers/zip_with_dockerfile.py: service.json keys
  (architecture, init.entry_path, api, resources).
- src/commands/packer/zip_with_dockerfile/prepare_directory.py: the packer
  prefixes each COPY source that starts with "." with "service/". The build
  context is .service/, and service/ holds the files of pack_config.json
  "include".
"""
import json
import os
import re
import shlex
import unittest

from src import config
from tests.helpers import ROOT

SERVICES = ["", "dependencies/regresion_cnf", "dependencies/random_cnf_generator",
            "solvers/frontier", "solvers/frontier_http"]
# One pack root per architecture: <service>/<arch>/.service (see test_layout.py).
ARCHES = ("amd64", "arm64")

# The port constant of each start.py (the sorter has src/config.py PORT).
PORT_RE = re.compile(r"^PORT = (\d+)", re.MULTILINE)


def service_dir(name):
    """The shared source directory of a service."""
    return ROOT / name if name else ROOT


def pack_root(name, arch):
    return service_dir(name) / arch


def load(name, file, arch="amd64"):
    with open(pack_root(name, arch) / ".service" / file) as f:
        return json.load(f)


def copy_sources(dockerfile: str):
    """The context sources of the COPY lines without --from."""
    for line in dockerfile.splitlines():
        parts = shlex.split(line) if line.strip().startswith("COPY ") else []
        if not parts or any(p.startswith("--from") for p in parts):
            continue
        sources = [p for p in parts[1:-1] if not p.startswith("--")]
        yield from sources


class ManifestsTest(unittest.TestCase):

    def test_one_architecture_per_pack_root(self):
        for arch in ARCHES:
            architectures = {load(name, "service.json", arch)["architecture"] for name in SERVICES}
            self.assertEqual(architectures, {f"linux/{arch}"}, arch)

    def test_entry_path_is_an_executable_included_file(self):
        for name in SERVICES:
            for arch in ARCHES:
                self._check_entry_path(name, arch)

    def _check_entry_path(self, name, arch):
            spec = load(name, "service.json", arch)
            pack = load(name, "pack_config.json", arch)
            entry = spec["init"]["entry_path"]
            self.assertNotIn("entrypoint", spec, name)
            dockerfile = (pack_root(name, arch) / ".service" / "Dockerfile").read_text()
            # COPY service /<workdir>: the workdir is the first segment of the entry path.
            self.assertIn(f"COPY service /{entry[0]}", dockerfile, name)
            script = entry[-1]
            self.assertIn(script, pack["include"], name)
            path = pack_root(name, arch) / script
            self.assertTrue(os.access(path, os.X_OK), f"{path} is not executable")
            self.assertTrue(path.read_text().startswith("#!/bin/sh\n"), path)
            self.assertIn(f"cd /{entry[0]} ", path.read_text(), path)

    def test_include_and_copy_sources_exist(self):
        for name in SERVICES:
            for arch in ARCHES:
                self._check_include(name, arch)

    def _check_include(self, name, arch):
            pack = load(name, "pack_config.json", arch)
            for item in pack["include"]:
                self.assertTrue((pack_root(name, arch) / item).exists(), f"{name}/{arch}: include {item}")
            dockerfile = (pack_root(name, arch) / ".service" / "Dockerfile").read_text()
            for source in copy_sources(dockerfile):
                if source == "service":
                    continue
                self.assertTrue(source.startswith("./"), f"{name}: COPY {source} needs ./")
                self.assertIn(source[2:], pack["include"], f"{name}: COPY {source} is not in include")

    def test_api_ports(self):
        for name, arch in ((n, a) for n in SERVICES for a in ARCHES):
            ports = [slot["port"] for slot in load(name, "service.json", arch)["api"]]
            self.assertEqual(len(ports), 1, name)
            if name:
                code_port = int(PORT_RE.search((service_dir(name) / "start.py").read_text()).group(1))
            else:
                code_port = config.PORT
            self.assertEqual(ports[0], code_port, name)

    def test_resources(self):
        for name, arch in ((n, a) for n in SERVICES for a in ARCHES):
            resources = load(name, "service.json", arch)["resources"]
            for key, value in resources["at_init"].items():
                self.assertGreaterEqual(resources["at_most"].get(key, value), value, f"{name}: {key}")

    def test_sorter_envs(self):
        for arch in ARCHES:
            self.assertEqual(load("", "service.json", arch)["envs"], list(config.DEFAULT_ENVS))

    def test_sorter_dependencies(self):
        for arch in ARCHES:
            self._check_sorter_dependencies(arch)

    def _check_sorter_dependencies(self, arch):
        pack = load("", "pack_config.json", arch)
        self.assertTrue(pack["dependencies_env"])
        self.assertFalse(pack["zip"])
        dependencies = pack["dependencies"]
        self.assertIn(config.REGRESSION_KEY, dependencies)
        self.assertIn(config.RANDOM_KEY, dependencies)
        self.assertTrue(any(key.startswith(config.SOLVER_KEY_PREFIX) for key in dependencies))
        for path in dependencies.values():
            self.assertTrue((pack_root("", arch) / path / ".service" / "service.json").exists(), path)
        # src/config.py uses the same directory names as pack_config.json.
        self.assertEqual(pack["service_dependencies_directory"], config.SERVICES_DIR.name)
        self.assertEqual(pack["metadata_dependencies_directory"], config.METADATA_DIR.name)
        self.assertEqual(pack["blocks_directory"], config.BLOCK_DIR.name)


if __name__ == "__main__":
    unittest.main()
