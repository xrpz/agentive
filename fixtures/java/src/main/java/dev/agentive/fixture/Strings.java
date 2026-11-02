package dev.agentive.fixture;

/** agentive self-test fixture for Java. */
public final class Strings {
    private Strings() {
    }

    /** Returns s reversed; null stays null. */
    public static String reverse(String s) {
        if (s == null) {
            return null;
        }
        return new StringBuilder(s).reverse().toString();
    }
}
