import tempfile
import unittest
from pathlib import Path
import sys

from core.memory import Memory
from core.platform_utils import get_config_path, get_database_path, resolve_executable


class TestPlatformUtils(unittest.TestCase):
    def test_project_paths_are_rooted(self):
        config_path = get_config_path()
        db_path = get_database_path()
        self.assertTrue(config_path.name == "config.yaml")
        self.assertTrue(db_path.name == "epet.db")
        self.assertTrue(config_path.is_absolute())
        self.assertTrue(db_path.is_absolute())

    def test_resolve_executable_accepts_current_python(self):
        resolved = resolve_executable(sys.executable)
        self.assertIsNotNone(resolved)
        self.assertTrue(Path(resolved).exists())

    def test_memory_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_db = Path(tmpdir) / "nested" / "data" / "epet.db"
            memory = Memory(str(nested_db))
            try:
                memory.set("hello", "world")
                self.assertEqual(memory.get("hello"), "world")
                self.assertTrue(nested_db.exists())
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
