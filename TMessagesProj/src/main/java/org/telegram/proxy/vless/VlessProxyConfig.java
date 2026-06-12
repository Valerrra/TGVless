package org.telegram.proxy.vless;

import android.net.Uri;
import android.text.TextUtils;

import androidx.annotation.IntDef;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import org.json.JSONException;
import org.json.JSONObject;

import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.util.Locale;
import java.util.UUID;

public class VlessProxyConfig {
    public static final String JSON_TYPE = "vless";

    @Retention(RetentionPolicy.SOURCE)
    @IntDef({TRANSPORT_TCP, TRANSPORT_WS})
    public @interface Transport {
    }

    public static final int TRANSPORT_TCP = 1;
    public static final int TRANSPORT_WS = 2;

    public final String server;
    public final int port;
    public final String uuid;
    public final boolean tlsEnabled;
    public final String sni;
    public final @Transport int transport;
    public final String wsPath;
    public final boolean insecure;
    public final int localPort;

    public VlessProxyConfig(
            @NonNull String server,
            int port,
            @NonNull String uuid,
            boolean tlsEnabled,
            @Nullable String sni,
            @Transport int transport,
            @Nullable String wsPath,
            boolean insecure,
            int localPort
    ) {
        this.server = server.trim();
        this.port = port;
        this.uuid = uuid.trim();
        this.tlsEnabled = tlsEnabled;
        this.sni = sni != null ? sni.trim() : "";
        this.transport = transport;
        this.wsPath = normalizeWsPath(wsPath);
        this.insecure = insecure;
        this.localPort = localPort;
    }

    public void validate() {
        validate(true);
    }

    public void validate(boolean requireLocalPort) {
        if (TextUtils.isEmpty(server)) {
            throw new IllegalArgumentException("Server is empty");
        }
        if (port <= 0 || port > 65535) {
            throw new IllegalArgumentException("Port is invalid");
        }
        if (requireLocalPort && (localPort <= 0 || localPort > 65535)) {
            throw new IllegalArgumentException("Local port is invalid");
        }
        try {
            UUID.fromString(uuid);
        } catch (Throwable t) {
            throw new IllegalArgumentException("UUID is invalid", t);
        }
        if (transport != TRANSPORT_TCP && transport != TRANSPORT_WS) {
            throw new IllegalArgumentException("Transport is invalid");
        }
        if (transport == TRANSPORT_WS && TextUtils.isEmpty(wsPath)) {
            throw new IllegalArgumentException("WebSocket path is empty");
        }
    }

    public @NonNull JSONObject toJson() throws JSONException {
        JSONObject json = new JSONObject();
        json.put("type", JSON_TYPE);
        json.put("server", server);
        json.put("port", port);
        json.put("uuid", uuid);
        json.put("tls", tlsEnabled);
        json.put("sni", sni);
        json.put("transport", transport == TRANSPORT_WS ? "ws" : "tcp");
        json.put("wsPath", wsPath);
        json.put("insecure", insecure);
        json.put("localPort", localPort);
        return json;
    }

    public @NonNull String toExportJsonString() throws JSONException {
        JSONObject json = toJson();
        json.remove("localPort");
        return json.toString(2);
    }

    public @NonNull String toUriString() {
        StringBuilder builder = new StringBuilder("vless://");
        builder.append(Uri.encode(uuid)).append("@").append(server).append(":").append(port);

        StringBuilder query = new StringBuilder();
        appendQueryParam(query, "type", transport == TRANSPORT_WS ? "ws" : "tcp");
        if (tlsEnabled) {
            appendQueryParam(query, "security", "tls");
        }
        if (!TextUtils.isEmpty(sni)) {
            appendQueryParam(query, "sni", sni);
        }
        if (transport == TRANSPORT_WS) {
            appendQueryParam(query, "path", normalizeWsPath(wsPath));
        }
        if (insecure) {
            appendQueryParam(query, "allowInsecure", "1");
        }
        if (query.length() > 0) {
            builder.append("?").append(query);
        }
        return builder.toString();
    }

    public static @NonNull VlessProxyConfig fromJson(@NonNull String jsonString) throws JSONException {
        return fromJson(new JSONObject(jsonString));
    }

    public static @NonNull VlessProxyConfig fromJson(@NonNull JSONObject json) throws JSONException {
        String transportValue = json.optString("transport", "tcp");
        @Transport int transport = "ws".equalsIgnoreCase(transportValue) ? TRANSPORT_WS : TRANSPORT_TCP;
        VlessProxyConfig config = new VlessProxyConfig(
                json.getString("server"),
                json.getInt("port"),
                json.getString("uuid"),
                json.optBoolean("tls", true),
                json.optString("sni", ""),
                transport,
                json.optString("wsPath", transport == TRANSPORT_WS ? "/" : ""),
                json.optBoolean("insecure", false),
                json.optInt("localPort", 0)
        );
        config.validate(false);
        return config;
    }

    public static @NonNull VlessProxyConfig fromUri(@NonNull String uriString) {
        Uri uri = Uri.parse(uriString.trim());
        if (!"vless".equalsIgnoreCase(uri.getScheme())) {
            throw new IllegalArgumentException("Scheme is invalid");
        }
        String uuid = uri.getUserInfo();
        String server = uri.getHost();
        int port = uri.getPort();
        if (TextUtils.isEmpty(uuid) || TextUtils.isEmpty(server) || port <= 0) {
            throw new IllegalArgumentException("VLESS URI is invalid");
        }

        String typeValue = firstNonEmpty(uri.getQueryParameter("type"), uri.getQueryParameter("transport"));
        @Transport int transport = "ws".equalsIgnoreCase(typeValue) ? TRANSPORT_WS : TRANSPORT_TCP;
        String securityValue = uri.getQueryParameter("security");
        boolean tlsEnabled = TextUtils.isEmpty(securityValue) || "tls".equalsIgnoreCase(securityValue);
        String sni = firstNonEmpty(uri.getQueryParameter("sni"), uri.getQueryParameter("host"));
        String wsPath = firstNonEmpty(uri.getQueryParameter("path"), uri.getQueryParameter("wsPath"));
        boolean insecure = parseBooleanQuery(uri.getQueryParameter("allowInsecure")) || parseBooleanQuery(uri.getQueryParameter("insecure"));

        VlessProxyConfig config = new VlessProxyConfig(
                server,
                port,
                uuid,
                tlsEnabled,
                sni,
                transport,
                wsPath,
                insecure,
                0
        );
        config.validate(false);
        return config;
    }

    public @NonNull VlessProxyConfig withLocalPort(int newLocalPort) {
        return new VlessProxyConfig(server, port, uuid, tlsEnabled, sni, transport, wsPath, insecure, newLocalPort);
    }

    public static @NonNull String normalizeWsPath(@Nullable String path) {
        String value = path != null ? path.trim() : "";
        if (TextUtils.isEmpty(value)) {
            return "/";
        }
        return value.charAt(0) == '/' ? value : "/" + value;
    }

    private static void appendQueryParam(@NonNull StringBuilder builder, @NonNull String key, @NonNull String value) {
        if (builder.length() > 0) {
            builder.append("&");
        }
        builder.append(Uri.encode(key)).append("=").append(Uri.encode(value));
    }

    private static @Nullable String firstNonEmpty(@Nullable String first, @Nullable String second) {
        if (!TextUtils.isEmpty(first)) {
            return first;
        }
        return TextUtils.isEmpty(second) ? null : second;
    }

    private static boolean parseBooleanQuery(@Nullable String value) {
        if (TextUtils.isEmpty(value)) {
            return false;
        }
        String normalized = value.trim().toLowerCase(Locale.US);
        return "1".equals(normalized) || "true".equals(normalized) || "yes".equals(normalized);
    }
}
