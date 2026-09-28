"""Push-only Web listeners survive a Hub restart without an RPC or an order."""
import queue
import threading
import time
from types import SimpleNamespace

import pytest

import cfquant_web_server as web
from cfquant.pipe_transport import dumps_pipe_message, loads_pipe_message
from cfquant.protocol import pack_event


class MemoryHub:
    def __init__(self):
        self.available = True
        self.receivers = {}
        self.connections = []

    def connect(self, *args, **kwargs):
        if not self.available:
            raise OSError("offline hub")
        hub = self

        class Connection:
            def __init__(self):
                self.incoming = queue.Queue()
                self.closed = False

            def write_frame(self, raw):
                envelope = loads_pipe_message(raw)
                assert envelope['type'] == 'hello', 'monitor must never send an RPC'
                if envelope['role'] == 'api_rx':
                    hub.receivers[envelope['client_id']] = self

            def read_frame(self):
                return self.incoming.get(timeout=3)

            def close(self):
                self.closed = True
                self.incoming.put(None)

        connection = Connection()
        self.connections.append(connection)
        return connection

    def disconnect(self):
        self.available = False
        for connection in self.connections:
            connection.close()
        self.receivers.clear()

    def publish(self, channel, bridge):
        self.receivers[channel].incoming.put(dumps_pipe_message({
            'type': 'delivery',
            'payload': pack_event('trader:on_stock_order', {
                'account_id': 'OFFLINE', 'order_id': 123, 'order_status': 54,
            }, meta={'bridge_id': bridge}),
        }))


def eventually(predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


@pytest.fixture
def listeners(monkeypatch):
    hub = MemoryHub()
    monkeypatch.setattr('cfquant.pipe_client.connect_pipe', hub.connect)
    monkeypatch.setattr(web, 'CLIENTS', SimpleNamespace(
        add_callback=lambda *args: None, remove_callback=lambda *args: None))
    store = web.CallbackEventStore(channels=['hgt.callback', 'sgt.callback'])
    store._pipe_retry_interval = .02
    monkeypatch.setattr(store, '_start_lttx_client', lambda: 0)
    received = queue.Queue()
    monkeypatch.setattr(store, '_append', lambda event, source: received.put((event, source)))
    yield store, hub, received
    store.close()


def test_push_listener_recovers_after_hub_restart_without_rpc(listeners):
    store, hub, received = listeners
    store.start()
    hub.publish('hgt.callback', 'hgt')
    assert received.get(timeout=1)[0]['meta']['bridge_id'] == 'hgt'
    hub.disconnect()
    eventually(lambda: all(not c._started for c in store._pipe_clients.values()))
    hub.available = True
    eventually(lambda: set(hub.receivers) == set(store.channels))
    assert len(hub.connections) == 8  # Two channels, one rx/tx pair per generation.
    hub.publish('hgt.callback', 'hgt')
    hub.publish('sgt.callback', 'sgt')
    events = [received.get(timeout=1), received.get(timeout=1)]
    assert {event['meta']['bridge_id'] for event, _ in events} == {'hgt', 'sgt'}
    assert all(source == 'channel' for _, source in events)
    assert received.empty(), 'reconnect must not register duplicate handlers'


def test_listener_retries_when_hub_was_absent_at_start(listeners):
    store, hub, received = listeners
    hub.available = False
    store.start()
    hub.available = True
    eventually(lambda: set(hub.receivers) == set(store.channels))
    hub.publish('sgt.callback', 'sgt')
    assert received.get(timeout=1)[0]['event'] == 'trader:on_stock_order'


def test_closed_store_does_not_reconnect_and_can_restart(listeners):
    store, hub, received = listeners
    store.start()
    store.close()
    assert all(connection.closed for connection in hub.connections)
    connection_count = len(hub.connections)
    time.sleep(.08)
    assert len(hub.connections) == connection_count
    store.start()
    hub.publish('hgt.callback', 'hgt')
    assert received.get(timeout=1)[0]['meta']['bridge_id'] == 'hgt'
    assert received.empty()


def test_same_channel_refresh_keeps_recovery_active_and_switch_stops_old(listeners):
    store, hub, received = listeners
    store.start()
    hub.disconnect()
    eventually(lambda: all(not c._started for c in store._pipe_clients.values()))
    store.refresh_channels(list(store.channels))
    hub.available = True
    eventually(lambda: set(hub.receivers) == set(store.channels))
    old_connections = list(hub.connections)
    store.refresh_channels(['new.callback'])
    assert all(connection.closed for connection in old_connections)
    hub.publish('new.callback', 'new')
    assert received.get(timeout=1)[0]['meta']['bridge_id'] == 'new'
    assert set(store._pipe_clients) == {'new.callback'}


def test_close_during_reconnect_closes_partial_pair_and_stops_worker(listeners, monkeypatch):
    store, hub, received = listeners
    store.start()
    hub.disconnect()
    eventually(lambda: all(not c._started for c in store._pipe_clients.values()))
    entered, release = threading.Event(), threading.Event()
    original_connect = hub.connect

    def blocked_connect(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(2)
        return original_connect(*args, **kwargs)

    monkeypatch.setattr('cfquant.pipe_client.connect_pipe', blocked_connect)
    hub.available = True
    assert entered.wait(2)
    worker = store._pipe_thread
    closing = threading.Thread(target=store.close)
    closing.start()
    try:
        assert store._pipe_stop.wait(1)
    finally:
        release.set()
        closing.join(2)
    assert not closing.is_alive()
    assert not worker.is_alive()
    assert not store._pipe_clients
    assert all(connection.closed for connection in hub.connections)
