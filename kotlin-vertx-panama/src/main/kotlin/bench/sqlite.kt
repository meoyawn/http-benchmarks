import bench.NewPost
import bench.Post
import sqlite.SQLite3Conn

fun SQLite3Conn.insertUser(email: String): Unit =
    prepareCached("INSERT OR IGNORE INTO users (email) VALUES (?)")
        .exec(arrayOf(email))

fun SQLite3Conn.insertPost(req: NewPost): Post =
    prepareCached(
        """
        INSERT INTO posts   (content,   user_id)
        SELECT              ?,          id
        FROM        users
        WHERE       email = ?
        RETURNING   id, user_id, content, created_at, updated_at
        """
    ).queryFirst(arrayOf(req.content, req.email)) {
        Post(
            id = it.getLong(0),
            user_id = it.getLong(1),
            content = it.getString(2),
            created_at = it.getLong(3),
            updated_at = it.getLong(4),
        )
    }
