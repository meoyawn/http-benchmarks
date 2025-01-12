package sqlite

import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertAll
import java.lang.foreign.Arena

class SQLite3ConnTest {

    data class SingleString(
        val str: String
    )

    @Test
    fun prepare(): Unit = Arena.ofConfined().use { arena ->
        SQLite3Conn.openMemory(arena).use { conn ->
            conn.prepare(sql = "SELECT ? as str").use { stmt ->
                assertAll({
                    val req = "HELLO"
                    assertThat(stmt.queryRow(arrayOf(req)) { it.getString(0) })
                        .isEqualTo(req)

                    assertThat(stmt.queryRow(arrayOf(req)) { it.get(SingleString::class.java) })
                        .isEqualTo(SingleString(req))
                })

                assertAll({
                    val i = 1
                    assertThatThrownBy { stmt.queryRow(arrayOf("HELLO")) { it.getString(i) } }
                        .isInstanceOf(IndexOutOfBoundsException::class.java)
                        .hasMessageContaining(i.toString())
                })

                assertThatThrownBy { stmt.exec() }
                    .isInstanceOf(IllegalArgumentException::class.java)
                    .hasMessageContainingAll("0", "1")

                assertAll({
                    val req = 123
                    assertThat(stmt.queryRow(arrayOf(req)) { it.getInt(0) })
                        .isEqualTo(req)
                })

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
                PRAGMA synchronous = NORMAL;
                """
            )

            assertAll({
                val sql = "jibberish"
                assertThatThrownBy { conn.exec(sql) }
                    .isInstanceOf(SQLite3Exception::class.java)
                    .hasMessageContaining(sql)
            })
        }
    }
}
