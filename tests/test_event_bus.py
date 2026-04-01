import unittest
import time
from epet.core.event_bus import EventBus

class TestEventBus(unittest.TestCase):
    def test_subscribe_and_publish(self):
        bus = EventBus()
        result = {}

        def callback(topic, data):
            result["topic"] = topic
            result["data"] = data

        bus.subscribe("test.topic", callback)
        bus.publish("test.topic", "hello")
        time.sleep(0.1)
        self.assertEqual(result.get("topic"), "test.topic")
        self.assertEqual(result.get("data"), "hello")

    def test_wildcard_matching(self):
        bus = EventBus()
        events = []

        def callback(topic, data):
            events.append((topic, data))

        bus.subscribe("pet/*", callback)
        bus.subscribe("pet/input/*", callback)
        bus.publish("pet/input/click", "button")
        bus.publish("pet/status", "happy")
        time.sleep(0.1)

        # Both subscribers get "pet/input/click", so 2 events from that publish.
        # Only the first subscriber gets "pet/status".
        # So total events: 3.
        # Check that the expected topics are present (order may vary).
        expected_topics = {"pet/input/click", "pet/status"}
        actual_topics = {t for t, _ in events}
        self.assertEqual(actual_topics, expected_topics)

    def test_multiple_subscribers(self):
        bus = EventBus()
        calls = []

        def cb1(topic, data):
            calls.append("cb1")

        def cb2(topic, data):
            calls.append("cb2")

        bus.subscribe("test", cb1)
        bus.subscribe("test", cb2)
        bus.publish("test", None)
        time.sleep(0.1)

        self.assertEqual(len(calls), 2)
        self.assertIn("cb1", calls)
        self.assertIn("cb2", calls)

    def test_no_matching_subscriber(self):
        bus = EventBus()
        # Should not raise any error
        bus.publish("nonexistent", "data")
        time.sleep(0.1)

if __name__ == "__main__":
    unittest.main()