package bench

import io.vertx.core.Vertx
import io.vertx.kotlin.coroutines.coAwait
import io.vertx.pgclient.PgBuilder
import io.vertx.pgclient.PgConnectOptions
import io.vertx.pgclient.impl.PgPoolOptions
import io.vertx.sqlclient.Pool
import io.vertx.sqlclient.SqlConnection
import io.vertx.sqlclient.Tuple

private const val MIGRATION = """
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE IF NOT EXISTS users (
  id          SERIAL8   NOT NULL PRIMARY KEY,
  email       CITEXT    NOT NULL UNIQUE,
  created_at  INT8      NOT NULL DEFAULT EXTRACT(EPOCH FROM clock_timestamp()) * 1000,
  updated_at  INT8      NOT NULL DEFAULT EXTRACT(EPOCH FROM clock_timestamp()) * 1000
);

CREATE TABLE IF NOT EXISTS posts (
  id          SERIAL8   NOT NULL PRIMARY KEY,
  user_id     INT8      NOT NULL REFERENCES users(id),
  content     TEXT      NOT NULL CHECK (content <> ''),
  created_at  INT8      NOT NULL DEFAULT EXTRACT(EPOCH FROM clock_timestamp()) * 1000,
  updated_at  INT8      NOT NULL DEFAULT EXTRACT(EPOCH FROM clock_timestamp()) * 1000
);
"""

suspend fun mkPG(vertx: Vertx): Pool =
    PgBuilder.pool()
        .connectingTo(PgConnectOptions().apply {
            host = "localhost"
            port = 5432
            user = "postgres"
            password = "postgres"
            cachePreparedStatements = true
        })
        .with(PgPoolOptions().apply {
            maxSize = Runtime.getRuntime().availableProcessors() * 2 + 1
        })
        .using(vertx)
        .build()
        .also {
            it.transact {
                for (s in MIGRATION.splitToSequence(';')) {
                    query(s.trim()).execute().coAwait()
                }
            }
        }

suspend inline fun <T> Pool.transact(fn: SqlConnection.() -> T): T {
    val c = connection.coAwait()

    return try {
        val tx = c.begin().coAwait()
        try {
            fn(c).also {
                tx.commit().coAwait()
            }
        } catch (e: Throwable) {
            tx.rollback().coAwait()
            throw e
        }
    } finally {
        c.close().coAwait()
    }
}

suspend fun SqlConnection.insertUser(email: String): Unit =
    preparedQuery("INSERT OR IGNORE INTO users (email) VALUES ($1)")
        .execute(Tuple.of(email))
        .coAwait()
        .let { }

suspend fun SqlConnection.insertPost(req: NewPost): Post =
    preparedQuery(
        """
        INSERT INTO posts   (content,   user_id)
        SELECT              $1,          id
        FROM        users
        WHERE       email = $2
        RETURNING   id, user_id, content, created_at, updated_at
        """
    )
        .execute(Tuple.of(req.content, req.email))
        .coAwait()
        .single()
        .let {
            Post(
                id = it.getLong(0),
                user_id = it.getLong(1),
                content = it.getString(2),
                created_at = it.getLong(3),
                updated_at = it.getLong(4),
            )
        }
