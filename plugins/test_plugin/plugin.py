import logging

logger = logging.getLogger(__name__)

def start(bus, hal, memory, config):
    """Test plugin: subscribes to pet/system/test and then publishes the same event."""
    def on_test_event(topic, data):
        logger.info(f"Test plugin received event on {topic}: {data}")

    # Subscribe first to ensure we catch our own publication
    bus.subscribe("pet/system/test", on_test_event)
    # Publish the test event
    bus.publish("pet/system/test", {"message": "Hello from test plugin"})
    logger.info("Test plugin started and published pet/system/test event")