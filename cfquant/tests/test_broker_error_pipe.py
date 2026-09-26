"""Pipe regressions for broker messages that contain LTtx's ``|`` delimiter."""
import json
import queue
import threading
import pytest

from cfquant.pipe_client import PipeRpcClient
from cfquant.pipe_hub import CfquantPipeHub
from cfquant.pipe_transport import dumps_pipe_message, loads_pipe_message
from cfquant.protocol import pack_response


BROKER_ERROR = "[AGENT] [broker] -150906080|1|order category is rejected"


@pytest.fixture(autouse=True)
def isolated_hub_status(monkeypatch):
    # Never overwrite a live hub's runtime status from an offline test.
    monkeypatch.setattr(CfquantPipeHub, "_write_status", lambda self: None)


class FakePipeConnection(object):
    """In-memory framed pipe endpoint; no named-pipe connection is opened."""

    def __init__(self, frames=(), hold_open=None):
        self.frames = list(frames)
        self.hold_open = hold_open
        self.writes = []
        self.closed = False

    def read_frame(self):
        if self.frames:
            return self.frames.pop(0)
        if self.hold_open is not None:
            self.hold_open.wait(1.0)
        return None

    def write_frame(self, payload):
        self.writes.append(payload)

    def close(self):
        self.closed = True


def delivered_payload(connection):
    assert len(connection.writes) == 1
    envelope = loads_pipe_message(connection.writes[0])
    assert envelope["type"] == "delivery"
    return envelope["payload"]


def receive_response_with_pipe_client(payload):
    receive = FakePipeConnection([dumps_pipe_message({"type": "delivery", "payload": payload})])
    client = PipeRpcClient(pipe_name="offline", client_id="web")
    client._started = True
    client._rx_conn = receive
    generation = client._event_dispatcher.start()
    pending = queue.Queue(maxsize=1)
    client._pending["orders-1"] = pending
    client._recv_loop(receive, generation)
    return pending.get_nowait()


def receive_event_with_pipe_client(payload):
    callback_done = threading.Event()
    release_receive = threading.Event()
    received = []
    receive = FakePipeConnection(
        [dumps_pipe_message({"type": "delivery", "payload": payload})],
        hold_open=release_receive,
    )
    client = PipeRpcClient(pipe_name="offline", client_id="callbacks")
    client.add_callback("__event__", lambda event: (received.append(event), callback_done.set()))
    client._started = True
    client._rx_conn = receive
    generation = client._event_dispatcher.start()
    thread = threading.Thread(target=client._recv_loop, args=(receive, generation))
    thread.start()
    try:
        assert callback_done.wait(1.0)
    finally:
        release_receive.set()
        thread.join(1.0)
        client.close()
    return received[0]


def test_pipe_response_with_broker_delimiter_resolves_pending_request():
    """Changing hub parsing back to unconditional `split('|', 1)` times this out."""
    hub = CfquantPipeHub(pipe_name="offline", show=False)
    web_receive = FakePipeConnection()
    hub._remember_client(web_receive, "web", receive_conn=True)
    hub.pending["orders-1"] = {
        "conn": web_receive,
        "action": "xttrader.query_stock_orders",
        "request_channel": "orders",
        "api_received_at": 1.0,
        "forward_done_at": 1.0,
    }
    raw = pack_response("orders-1", result=[{"status_msg": BROKER_ERROR}])

    hub._handle_qmt_publish(FakePipeConnection(), {"payload": raw, "channel": "web"})

    assert "orders-1" not in hub.pending
    response = receive_response_with_pipe_client(delivered_payload(web_receive))
    assert response["type"] == "response"
    assert response["id"] == "orders-1"
    assert response["result"] == [{"status_msg": BROKER_ERROR}]


def test_pipe_raw_order_error_with_broker_delimiter_reaches_receiver_as_order_event():
    """The raw QMT callback fallback must preserve its event name and payload."""
    hub = CfquantPipeHub(pipe_name="offline", show=False)
    web_receive = FakePipeConnection()
    hub._remember_client(web_receive, "cfquant.callback.event", receive_conn=True)
    raw_callback = {
        "type": "event",
        "event": "trader:on_order_error",
        "account_id": "TEST",
        "account_type": "HGT",
        "data": {"account_id": "TEST", "account_type": "HGT", "error_msg": BROKER_ERROR},
    }

    hub._handle_qmt_publish(
        FakePipeConnection(),
        {"payload": json.dumps(raw_callback), "channel": "cfquant.callback.event"},
    )

    event = receive_event_with_pipe_client(delivered_payload(web_receive))
    assert event["event"] == "trader:on_order_error"
    assert event["data"] == raw_callback
