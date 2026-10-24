"""GPU priority: background model calls wait for foreground ones."""
import json
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import gpu_gate


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class GateTest(unittest.TestCase):
    def test_background_waits_for_foreground_and_grace(self):
        clock = Clock()
        gate = gpu_gate.GpuGate(grace_seconds=2, max_wait_seconds=60, clock=clock)
        order = []
        with gate.foreground():
            done = threading.Event()

            def background():
                with gate.background():
                    order.append("background")
                done.set()

            t = threading.Thread(target=background, daemon=True)
            t.start()
            time.sleep(0.3)
            order.append("foreground")
        time.sleep(0.3)
        self.assertEqual(["foreground"], order)  # still inside the grace period
        clock.now += 2.5
        self.assertTrue(done.wait(2))
        self.assertEqual(["foreground", "background"], order)

    def test_one_background_call_at_a_time_and_no_starvation(self):
        clock = Clock()
        gate = gpu_gate.GpuGate(grace_seconds=0, max_wait_seconds=10, clock=clock)
        entered = threading.Event()
        with gate.background():
            t = threading.Thread(target=lambda: (gate.background().__enter__(), entered.set()),
                                 daemon=True)
            t.start()
            time.sleep(0.3)
            self.assertFalse(entered.is_set())
        self.assertTrue(entered.wait(2))
        gate.background_active = False
        busy = threading.Event()

        def chat():
            with gate.foreground():
                busy.wait(5)

        threading.Thread(target=chat, daemon=True).start()
        time.sleep(0.1)
        forced = threading.Event()
        threading.Thread(target=lambda: (gate.background().__enter__(), forced.set()),
                         daemon=True).start()
        time.sleep(0.3)
        self.assertFalse(forced.is_set())
        clock.now += 11  # waited past ADAPTER_GPU_MAX_WAIT_SECONDS
        self.assertTrue(forced.wait(2))
        self.assertEqual(1, gate.stats()["counts"]["background_forced"])
        busy.set()


class Upstream(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        data = json.dumps({"message": {"content": body.decode()}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class BrokerTest(unittest.TestCase):
    def setUp(self):
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()
        self.gate = gpu_gate.GpuGate(grace_seconds=0, max_wait_seconds=30)
        self.broker = gpu_gate.start_broker(
            self.gate, "http://127.0.0.1:%d" % self.upstream.server_port, 0)
        self.url = "http://127.0.0.1:%d" % self.broker.server_port

    def tearDown(self):
        for server in (self.broker, self.upstream):
            server.shutdown()
            server.server_close()

    def post(self, path, body):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_forwards_after_foreground_and_refuses_other_paths(self):
        result = {}
        with self.gate.foreground():
            t = threading.Thread(target=lambda: result.update(
                r=self.post("/api/chat", {"model": "m", "stream": False})), daemon=True)
            t.start()
            time.sleep(0.4)
            self.assertNotIn("r", result)
        t.join(5)
        self.assertEqual(200, result["r"][0])
        self.assertIn('"model": "m"', result["r"][1]["message"]["content"])
        self.assertEqual(404, self.post("/api/pull", {"name": "x"})[0])
        self.assertEqual(400, self.post("/api/chat", {"stream": True})[0])


if __name__ == "__main__":
    unittest.main()
