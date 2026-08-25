"""Short request reads reuse the process-owned PostgreSQL pool."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from mari_server.persistence.postgres import connection, database


class RequestConnectionPoolTests(unittest.TestCase):
    def test_request_connection_returns_the_lease_and_releases_it(self) -> None:
        leased = object()
        lease = MagicMock()
        lease.__enter__.return_value = leased
        process_pool = MagicMock()
        process_pool.connection.return_value = lease

        with patch.object(connection, "pool", return_value=process_pool):
            with connection.request_connection() as received:
                self.assertIs(received, leased)

        process_pool.connection.assert_called_once_with()
        lease.__exit__.assert_called_once()

    def test_open_pool_waits_until_the_warm_connection_is_ready(self) -> None:
        process_pool = MagicMock()
        with patch.object(database.postgres, "pool", return_value=process_pool):
            database.open_pool()
        process_pool.wait.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
