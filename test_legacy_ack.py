"""The legacy TCP server must acknowledge successful handling, never just receipt."""

import socket
import unittest

from app.net import protocol
from app.net.server import MessageServer


class LegacyAckTests(unittest.TestCase):
    def setUp(self):
        self.server_socket, self.client_socket = socket.socketpair()
        self.client_socket.setblocking(False)
        self.addCleanup(self.server_socket.close)
        self.addCleanup(self.client_socket.close)
        self.packet = protocol.parse_packet(
            protocol.make_message("Alice", "persist first", msg_id="ack-order"),
            protocol.TYPES_TCP,
        )

    def test_ack_becomes_readable_only_after_handler_completes(self):
        received = []

        def store(packet, ip):
            with self.assertRaises(BlockingIOError):
                self.client_socket.recv(65536)
            received.append(packet["text"])

        server = MessageServer("Bob", on_message=store)
        server._dispatch(self.packet, "127.0.0.1", self.server_socket)
        self.assertEqual(received, ["persist first"])
        frames = protocol.FrameDecoder().feed(self.client_socket.recv(65536))
        ack = protocol.parse_packet(frames[0], protocol.TYPES_TCP)
        self.assertEqual(ack["type"], "ack")
        self.assertEqual(ack["ack_for"], "ack-order")

    def test_failed_handler_does_not_send_ack(self):
        def failed_store(packet, ip):
            raise OSError("database write failed")

        server = MessageServer("Bob", on_message=failed_store)
        try:
            server._dispatch(self.packet, "127.0.0.1", self.server_socket)
        except OSError:
            pass
        with self.assertRaises(BlockingIOError):
            self.client_socket.recv(65536)


if __name__ == "__main__":
    unittest.main()
