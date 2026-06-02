package dev.meshflow;

import java.util.List;
import java.util.Map;

/**
 * Minimal JSON serialiser — no external dependencies.
 *
 * <p>Handles the value types produced by {@link MeshFlowClient} request
 * builders: {@code String}, {@code Number}, {@code Boolean}, {@code null},
 * {@code Map<String,Object>}, and {@code List<Object>}.
 * This class is package-private; callers should use {@link MeshFlowClient}.
 */
final class JsonSerializer {

    private JsonSerializer() {}

    /**
     * Returns a JSON-encoded string literal, with special characters escaped.
     * The returned value includes surrounding double-quotes.
     */
    static String quoteString(String s) {
        if (s == null) return "null";
        StringBuilder sb = new StringBuilder(s.length() + 2);
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if      (c == '"')  sb.append("\\\"");
            else if (c == '\\') sb.append("\\\\");
            else if (c == '\n') sb.append("\\n");
            else if (c == '\r') sb.append("\\r");
            else if (c == '\t') sb.append("\\t");
            else if (c < 0x20)  sb.append(String.format("\\u%04x", (int) c));
            else                sb.append(c);
        }
        sb.append('"');
        return sb.toString();
    }

    /**
     * Serialises an arbitrary value to its JSON representation.
     *
     * <ul>
     *   <li>{@code null}        &rarr; {@code "null"}</li>
     *   <li>{@code String}      &rarr; quoted, escaped string</li>
     *   <li>{@code Boolean}     &rarr; {@code "true"} / {@code "false"}</li>
     *   <li>{@code Number}      &rarr; decimal representation</li>
     *   <li>{@code Map<String,?>} &rarr; JSON object</li>
     *   <li>{@code List<?>}     &rarr; JSON array</li>
     *   <li>anything else       &rarr; {@code toString()} quoted as a string</li>
     * </ul>
     */
    static String serializeValue(Object value) {
        if (value == null) return "null";
        if (value instanceof Boolean) return value.toString();
        if (value instanceof Number) {
            double d = ((Number) value).doubleValue();
            if (d == Math.floor(d) && !Double.isInfinite(d) && Math.abs(d) < 1e15) {
                return String.valueOf(((Number) value).longValue());
            }
            return String.valueOf(d);
        }
        if (value instanceof String) return quoteString((String) value);
        if (value instanceof Map) {
            @SuppressWarnings("unchecked")
            Map<String, Object> m = (Map<String, Object>) value;
            return serializeMap(m);
        }
        if (value instanceof List) {
            @SuppressWarnings("unchecked")
            List<Object> l = (List<Object>) value;
            return serializeList(l);
        }
        return quoteString(value.toString());
    }

    /** Serialises a {@code Map<String,Object>} to a JSON object string. */
    static String serializeMap(Map<String, Object> map) {
        if (map == null || map.isEmpty()) return "{}";
        StringBuilder sb = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, Object> e : map.entrySet()) {
            if (!first) sb.append(',');
            first = false;
            sb.append(quoteString(e.getKey()))
              .append(':')
              .append(serializeValue(e.getValue()));
        }
        sb.append('}');
        return sb.toString();
    }

    /** Serialises a {@code List<Object>} to a JSON array string. */
    static String serializeList(List<Object> list) {
        if (list == null || list.isEmpty()) return "[]";
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (Object item : list) {
            if (!first) sb.append(',');
            first = false;
            sb.append(serializeValue(item));
        }
        sb.append(']');
        return sb.toString();
    }
}
