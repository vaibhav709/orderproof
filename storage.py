"""A transaction shared by the order, its review history, and its checkpoint."""
import os
import sqlite3
from contextlib import contextmanager, closing
from pathlib import Path


def configured_database():
    url = os.environ.get("ORDERPROOF_DATABASE_URL")
    if url:
        if not url.startswith(("postgresql://", "postgres://")):
            raise ValueError("ORDERPROOF_DATABASE_URL must be a PostgreSQL connection URL")
        return url
    if os.environ.get("VERCEL"):
        raise RuntimeError("Set ORDERPROOF_DATABASE_URL for durable hosted storage")
    return Path(__file__).with_name("orderproof.sqlite3")


class Transaction:
    def __init__(self, connection, postgres):
        self.connection = connection
        self.postgres = postgres

    def execute(self, sql, params=()):
        return self.connection.execute(sql.replace("?", "%s") if self.postgres else sql, params)


@contextmanager
def transaction(database, *, write=False):
    postgres = isinstance(database, str) and database.startswith(("postgresql://", "postgres://"))
    if postgres:
        import psycopg
        connection = psycopg.connect(database, connect_timeout=10, prepare_threshold=None)
    else:
        connection = sqlite3.connect(database)
    with closing(connection), connection:
        if write and not postgres:
            connection.execute("BEGIN IMMEDIATE")
        yield Transaction(connection, postgres)
