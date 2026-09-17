package sqlite

import java.nio.file.Files
import java.nio.file.Path

/** The JAR contains the SQLite binary compiled for this build's OS and CPU. */
object NativeLibrary {
    init {
        val override = System.getProperty("sqlite.library")
        if (override != null) {
            System.load(Path.of(override).toAbsolutePath().toString())
        } else {
            val os = when (System.getProperty("os.name")) {
                "Mac OS X" -> "macos"
                "Linux" -> "linux"
                else -> error("SQLite native build supports macOS and Linux")
            }
            val arch = when (val name = System.getProperty("os.arch")) {
                "aarch64", "arm64" -> "aarch64"
                "amd64", "x86_64" -> "x86_64"
                else -> error("Unsupported CPU architecture: $name")
            }
            val name = if (os == "macos") "libsqlite3.dylib" else "libsqlite3.so"
            val resource = "/native/$os-$arch/$name"
            val input = checkNotNull(NativeLibrary::class.java.getResourceAsStream(resource)) {
                "No bundled SQLite for $os-$arch; build the JAR on the target platform"
            }
            val directory = Files.createTempDirectory("sqlite-ffm-")
            val library = directory.resolve(name)
            directory.toFile().deleteOnExit()
            library.toFile().deleteOnExit()
            input.use { Files.copy(it, library) }
            System.load(library.toAbsolutePath().toString())
        }
    }

    fun load() = Unit
}
