import unittest
import tempfile
import os
from epet.core.memory import Memory

class TestMemory(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.temp_db.close()
        self.memory = Memory(self.temp_db.name)

    def tearDown(self):
        self.memory.close()
        os.unlink(self.temp_db.name)

    def test_set_get(self):
        self.memory.set("key1", "value1")
        self.assertEqual(self.memory.get("key1"), "value1")
        self.assertIsNone(self.memory.get("nonexistent"))

    def test_log_event(self):
        self.memory.log_event("test_event", "test_data")
        # Verify via direct SQL
        cursor = self.memory.conn.cursor()
        cursor.execute("SELECT event_type, data FROM events")
        rows = cursor.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "test_event")
        self.assertEqual(rows[0][1], "test_data")

    def test_remember_recall(self):
        self.memory.remember("category1", "key1", "value1")
        self.assertEqual(self.memory.recall("category1", "key1"), "value1")
        self.assertIsNone(self.memory.recall("category1", "nonexistent"))
        self.assertIsNone(self.memory.recall("other_cat", "key1"))

    def test_overwrite(self):
        self.memory.set("key1", "value1")
        self.memory.set("key1", "value2")
        self.assertEqual(self.memory.get("key1"), "value2")

        self.memory.remember("cat", "k", "v1")
        self.memory.remember("cat", "k", "v2")
        self.assertEqual(self.memory.recall("cat", "k"), "v2")

if __name__ == "__main__":
    unittest.main()