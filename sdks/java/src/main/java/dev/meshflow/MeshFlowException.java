package dev.meshflow;

import java.io.IOException;

/**
 * Thrown when the MeshFlow server responds with a non-2xx HTTP status code.
 *
 * <p>Extends {@link IOException} so callers that declare {@code throws IOException}
 * do not need a separate catch block; callers that want to inspect the status
 * code can catch {@code MeshFlowException} specifically.
 */
public final class MeshFlowException extends IOException {

    private final int statusCode;
    private final String method;
    private final String path;
    private final String responseBody;

    /**
     * @param statusCode   the HTTP response status code
     * @param method       the HTTP verb (e.g. {@code "GET"}, {@code "POST"})
     * @param path         the request path (e.g. {@code "/run"})
     * @param responseBody the raw response body text
     */
    public MeshFlowException(int statusCode, String method, String path, String responseBody) {
        super("meshflow: " + method + " " + path + " returned HTTP " + statusCode +
              (responseBody != null && !responseBody.isEmpty() ? " — " + responseBody : ""));
        this.statusCode = statusCode;
        this.method = method;
        this.path = path;
        this.responseBody = responseBody;
    }

    /** The HTTP response status code (e.g. 400, 404, 500). */
    public int getStatusCode() { return statusCode; }

    /** The HTTP verb used for the failing request. */
    public String getMethod() { return method; }

    /** The request path that returned the error. */
    public String getPath() { return path; }

    /** The raw response body returned by the server, if any. */
    public String getResponseBody() { return responseBody; }
}
