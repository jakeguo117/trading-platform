from __future__ import annotations

from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from gld_simulation.bundle import load_raw_bundle
import tools.generate_gld_simulation_fixtures as generator
from tools.generate_gld_simulation_fixtures import generate


ROOT = Path(__file__).resolve().parents[1]
U64 = ROOT / "benchmarks" / "fixtures" / "u64_v0.1.json"


class GldSimulationFixtureGeneratorTests(unittest.TestCase):
    def test_generator_publishes_verified_tree_with_private_root_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "generated"
            generate(U64, output)
            self.assertTrue(output.is_dir())
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            for scenario in ("pass", "no_action", "no_decision"):
                load_raw_bundle(output / scenario)

    def test_generator_refuses_existing_file_directory_and_symlink(self) -> None:
        for existing_kind in ("file", "directory", "symlink"):
            with self.subTest(
                existing_kind=existing_kind
            ), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary)
                output = parent / "generated"
                sentinel = parent / "sentinel.txt"
                sentinel.write_text("preserve-me", encoding="utf-8")
                if existing_kind == "file":
                    output.write_text("user-file", encoding="utf-8")
                elif existing_kind == "directory":
                    output.mkdir()
                    (output / "user-data.txt").write_text(
                        "preserve-me", encoding="utf-8"
                    )
                else:
                    output.symlink_to(sentinel)
                with self.assertRaises(FileExistsError):
                    generate(U64, output)
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve-me")
                if existing_kind == "file":
                    self.assertEqual(output.read_text(encoding="utf-8"), "user-file")
                elif existing_kind == "directory":
                    self.assertEqual(
                        (output / "user-data.txt").read_text(encoding="utf-8"),
                        "preserve-me",
                    )
                else:
                    self.assertTrue(output.is_symlink())

    def test_generator_rejects_symlink_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real_parent = base / "real"
            real_parent.mkdir()
            linked_parent = base / "linked"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                generate(U64, linked_parent / "generated")
            self.assertFalse((real_parent / "generated").exists())

    def test_publish_race_cannot_replace_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            output = parent / "generated"
            original_publish = generator._publish_directory_no_clobber

            def race_publish(source: Path, destination: Path) -> None:
                destination.mkdir()
                (destination / "user-data.txt").write_text(
                    "preserve-me", encoding="utf-8"
                )
                original_publish(source, destination)

            with mock.patch.object(
                generator,
                "_publish_directory_no_clobber",
                side_effect=race_publish,
            ), self.assertRaises(FileExistsError):
                generate(U64, output)
            self.assertEqual(
                (output / "user-data.txt").read_text(encoding="utf-8"),
                "preserve-me",
            )
            self.assertEqual(
                list(parent.glob(".generated.tmp-*")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
