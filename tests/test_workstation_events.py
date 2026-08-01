"""M1 acceptance test: the live event channel.

Covers required check 9, plus the rule that an unauthenticated socket is never
upgraded.
"""

from __future__ import annotations

import unittest

from tests._workstation_doubles import TEST_TOKEN, WorkstationTestCase

SUBPROTOCOL = "cofferdam-token"


class EventChannelTests(WorkstationTestCase):
    def test_event_clients_receive_action_state_updates(self) -> None:
        """(9) Started and finished events reach a connected client."""
        with self.client.websocket_connect("/ws", subprotocols=[SUBPROTOCOL, TEST_TOKEN]) as websocket:
            hello = websocket.receive_json()
            self.assertEqual(hello["event"], "hello")
            self.assertEqual(hello["data"]["adapter"]["name"], "stub")

            response = self.client.post(
                "/api/actions",
                json={"action": "open_application", "params": {"application": "firefox"}},
                headers=self.auth,
            )
            self.assertEqual(response.status_code, 200)
            action_id = response.json()["action_id"]

            started = websocket.receive_json()
            self.assertEqual(started["event"], "action_started")
            self.assertEqual(started["data"]["action_id"], action_id)
            self.assertEqual(started["data"]["status"], "running")

            finished = websocket.receive_json()
            self.assertEqual(finished["event"], "action_finished")
            self.assertEqual(finished["data"]["action_id"], action_id)
            self.assertEqual(finished["data"]["status"], "succeeded")
            self.assertTrue(finished["data"]["finished_at"])

    def test_failed_actions_are_broadcast_too(self) -> None:
        with self.client.websocket_connect("/ws", subprotocols=[SUBPROTOCOL, TEST_TOKEN]) as websocket:
            websocket.receive_json()  # hello
            self.client.post(
                "/api/actions",
                json={"action": "open_url", "params": {"url": "https://example.com"}},
                headers=self.auth,
            )
            websocket.receive_json()  # started
            finished = websocket.receive_json()
            self.assertEqual(finished["event"], "action_finished")

    def test_heartbeat_round_trip(self) -> None:
        with self.client.websocket_connect("/ws", subprotocols=[SUBPROTOCOL, TEST_TOKEN]) as websocket:
            websocket.receive_json()  # hello
            websocket.send_text("ping")
            self.assertEqual(websocket.receive_json()["event"], "heartbeat")

    def test_token_may_also_be_supplied_as_a_query_parameter(self) -> None:
        with self.client.websocket_connect(f"/ws?token={TEST_TOKEN}") as websocket:
            self.assertEqual(websocket.receive_json()["event"], "hello")


class EventAuthenticationTests(WorkstationTestCase):
    def _assert_rejected(self, *args, **kwargs) -> None:
        from starlette.websockets import WebSocketDisconnect

        with self.assertRaises(WebSocketDisconnect) as caught:
            with self.client.websocket_connect(*args, **kwargs) as websocket:
                websocket.receive_json()
        self.assertEqual(caught.exception.code, 4401)

    def test_unauthenticated_event_connections_are_rejected(self) -> None:
        self._assert_rejected("/ws")

    def test_invalid_token_event_connections_are_rejected(self) -> None:
        self._assert_rejected("/ws", subprotocols=[SUBPROTOCOL, "wrong-token"])
        self._assert_rejected("/ws?token=wrong-token")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
