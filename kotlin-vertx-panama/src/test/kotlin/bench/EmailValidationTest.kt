package bench

import com.alibaba.fastjson2.JSON
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class EmailValidationTest {
    @Test
    fun sharedEmailValidationCases() {
        val cases = JSON.parseArray(Files.readString(Path.of("../testdata/email-validation.json")))
        for (index in cases.indices) {
            val case = cases.getJSONObject(index)
            val email = case.getString("email")
            assertThat(validEmail(email)).describedAs("email: %s", email)
                .isEqualTo(case.getBooleanValue("valid"))
        }
    }
}
