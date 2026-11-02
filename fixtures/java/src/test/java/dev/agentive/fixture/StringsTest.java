package dev.agentive.fixture;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

import org.junit.jupiter.api.Test;

class StringsTest {
    @Test
    void reversesText() {
        assertEquals("cba", Strings.reverse("abc"));
    }

    @Test
    void keepsNull() {
        assertNull(Strings.reverse(null));
    }
}
