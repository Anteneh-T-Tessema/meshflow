package dev.meshflow;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal recursive-descent JSON parser — no external dependencies.
 *
 * <p>Supports the full JSON value set (object, array, string, number,
 * boolean, null) sufficient for all MeshFlow server responses.
 * This class is package-private; callers should use {@link MeshFlowClient}.
 */
final class JsonParser {

    private final String src;
    private int pos;

    private JsonParser(String src) {
        this.src = src;
        this.pos = 0;
    }

    /** Parses a JSON object string into a {@code Map<String,Object>}. */
    static Map<String, Object> parseObject(String json) {
        if (json == null || json.isEmpty()) {
            return new LinkedHashMap<>();
        }
        JsonParser p = new JsonParser(json.trim());
        Object v = p.parseValue();
        if (v instanceof Map) {
            @SuppressWarnings("unchecked")
            Map<String, Object> m = (Map<String, Object>) v;
            return m;
        }
        return new LinkedHashMap<>();
    }

    /** Parses a JSON array string into a {@code List<Object>}. */
    static List<Object> parseArray(String json) {
        if (json == null || json.isEmpty()) {
            return new ArrayList<>();
        }
        JsonParser p = new JsonParser(json.trim());
        Object v = p.parseValue();
        if (v instanceof List) {
            @SuppressWarnings("unchecked")
            List<Object> l = (List<Object>) v;
            return l;
        }
        return new ArrayList<>();
    }

    // ── recursive descent ──────────────────────────────────────────────────────

    private Object parseValue() {
        skipWhitespace();
        if (pos >= src.length()) return null;
        char c = src.charAt(pos);
        if (c == '{') return parseObjectValue();
        if (c == '[') return parseArrayValue();
        if (c == '"') return parseString();
        if (c == 't') return parseLiteral("true", Boolean.TRUE);
        if (c == 'f') return parseLiteral("false", Boolean.FALSE);
        if (c == 'n') { parseLiteral("null", null); return null; }
        return parseNumber();
    }

    private Map<String, Object> parseObjectValue() {
        Map<String, Object> map = new LinkedHashMap<>();
        pos++; // consume '{'
        skipWhitespace();
        if (pos < src.length() && src.charAt(pos) == '}') { pos++; return map; }
        while (pos < src.length()) {
            skipWhitespace();
            String key = parseString();
            skipWhitespace();
            if (pos < src.length() && src.charAt(pos) == ':') pos++;
            Object val = parseValue();
            map.put(key, val);
            skipWhitespace();
            if (pos >= src.length()) break;
            char next = src.charAt(pos);
            if (next == '}') { pos++; break; }
            if (next == ',') { pos++; } // move to next pair
        }
        return map;
    }

    private List<Object> parseArrayValue() {
        List<Object> list = new ArrayList<>();
        pos++; // consume '['
        skipWhitespace();
        if (pos < src.length() && src.charAt(pos) == ']') { pos++; return list; }
        while (pos < src.length()) {
            list.add(parseValue());
            skipWhitespace();
            if (pos >= src.length()) break;
            char next = src.charAt(pos);
            if (next == ']') { pos++; break; }
            if (next == ',') { pos++; }
        }
        return list;
    }

    private String parseString() {
        pos++; // consume opening '"'
        StringBuilder sb = new StringBuilder();
        while (pos < src.length()) {
            char c = src.charAt(pos++);
            if (c == '"') break;
            if (c == '\\' && pos < src.length()) {
                char esc = src.charAt(pos++);
                if (esc == '"')  { sb.append('"'); }
                else if (esc == '\\') { sb.append('\\'); }
                else if (esc == '/')  { sb.append('/'); }
                else if (esc == 'n')  { sb.append('\n'); }
                else if (esc == 'r')  { sb.append('\r'); }
                else if (esc == 't')  { sb.append('\t'); }
                else if (esc == 'b')  { sb.append('\b'); }
                else if (esc == 'f')  { sb.append('\f'); }
                else if (esc == 'u' && pos + 4 <= src.length()) {
                    String hex = src.substring(pos, pos + 4);
                    pos += 4;
                    sb.append((char) Integer.parseInt(hex, 16));
                } else {
                    sb.append(esc);
                }
            } else {
                sb.append(c);
            }
        }
        return sb.toString();
    }

    private Object parseLiteral(String word, Object value) {
        if (src.startsWith(word, pos)) {
            pos += word.length();
        }
        return value;
    }

    private Number parseNumber() {
        int start = pos;
        if (pos < src.length() && src.charAt(pos) == '-') pos++;
        while (pos < src.length() && Character.isDigit(src.charAt(pos))) pos++;
        boolean isFloat = false;
        if (pos < src.length() && src.charAt(pos) == '.') {
            isFloat = true;
            pos++;
            while (pos < src.length() && Character.isDigit(src.charAt(pos))) pos++;
        }
        if (pos < src.length() && (src.charAt(pos) == 'e' || src.charAt(pos) == 'E')) {
            isFloat = true;
            pos++;
            if (pos < src.length() && (src.charAt(pos) == '+' || src.charAt(pos) == '-')) pos++;
            while (pos < src.length() && Character.isDigit(src.charAt(pos))) pos++;
        }
        String numStr = src.substring(start, pos);
        if (numStr.isEmpty()) return 0;
        try {
            if (isFloat) return Double.parseDouble(numStr);
            long l = Long.parseLong(numStr);
            if (l >= Integer.MIN_VALUE && l <= Integer.MAX_VALUE) return (int) l;
            return l;
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    private void skipWhitespace() {
        while (pos < src.length() && src.charAt(pos) <= ' ') pos++;
    }
}
