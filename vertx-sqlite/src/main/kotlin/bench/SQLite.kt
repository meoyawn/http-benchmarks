package bench

import org.sqlite.SQLiteConfig
import org.sqlite.SQLiteDataSource
import kotlin.time.Duration.Companion.seconds

object SQLite {

    private const val DB_PATH = "../db/db.sqlite"

    private const val URL = "jdbc:sqlite:$DB_PATH"

    val DATA_SOURCE = SQLiteDataSource(SQLiteConfig().apply {
        setReadOnly(false)
        transactionMode = SQLiteConfig.TransactionMode.IMMEDIATE
        busyTimeout = 10.seconds.inWholeMilliseconds.toInt()
        setSynchronous(SQLiteConfig.SynchronousMode.NORMAL)
        setJournalMode(SQLiteConfig.JournalMode.WAL)
        enforceForeignKeys(true)
    }).apply {
        url = URL
    }
}
