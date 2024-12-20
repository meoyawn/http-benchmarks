package sqlite

import org.junit.jupiter.api.Test
import java.lang.foreign.Arena
import kotlin.test.assertEquals

class SQLite3ConnTest {

    @Test
    fun prepare(): Unit = Arena.ofConfined().use { arena ->
        SQLite3Conn.openMemory(arena).use { conn ->
            conn.prepare(sql = "SELECT ?").use { stmt ->

                run {
                    val req = "HELLO"
                    val res = stmt.queryRow(arrayOf(req)) { it.getString(0) }
                    assertEquals(actual = res, expected = req)
                }

                run {
                    val req = 123
                    val res = stmt.queryRow(arrayOf(req)) { it.getInt(0) }
                    assertEquals(actual = res, expected = req)
                }

                run {
                    stmt.exec(arrayOf(123))
                }
            }
        }
    }

    @Test
    fun script(): Unit = Arena.ofConfined().use { arena ->
        SQLite3Conn.openMemory(arena).use { conn ->
            conn.exec(
                """
                PRAGMA journal_mode = WAL;
                """
            )
        }
    }
}
