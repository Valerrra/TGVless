package org.telegram.proxy.vless;

import android.text.TextUtils;

import androidx.annotation.NonNull;

import org.telegram.messenger.FileLog;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.Closeable;
import java.io.DataInputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Inet4Address;
import java.net.Inet6Address;
import java.net.InetAddress;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.KeyManagementException;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.security.cert.X509Certificate;
import java.util.ArrayDeque;
import java.util.Queue;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicBoolean;

import javax.net.ssl.HostnameVerifier;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSession;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

public class VlessConnection implements Runnable {
    private static final int CMD_CONNECT = 0x01;

    private final Socket clientSocket;
    private final VlessProxyConfig config;

    public VlessConnection(@NonNull Socket clientSocket, @NonNull VlessProxyConfig config) {
        this.clientSocket = clientSocket;
        this.config = config;
    }

    @Override
    public void run() {
        Socket upstreamSocket = null;
        Closeable extraCloseable = null;
        try {
            clientSocket.setTcpNoDelay(true);
            DataInputStream input = new DataInputStream(new BufferedInputStream(clientSocket.getInputStream()));
            OutputStream clientOutput = new BufferedOutputStream(clientSocket.getOutputStream());

            SocksRequest request = negotiateSocks(input, clientOutput);
            logInfo("VLESS CONNECT " + request.host + ":" + request.port + " via " + (config.transport == VlessProxyConfig.TRANSPORT_WS ? "ws" : "tcp"));

            UpstreamChannel channel = openUpstream(request.host, request.port);
            upstreamSocket = channel.socket;
            extraCloseable = channel.extraCloseable;

            writeSocksSuccess(clientOutput);
            pipeBothWays(clientSocket.getInputStream(), clientSocket.getOutputStream(), channel.inputStream, channel.outputStream);
            logInfo("VLESS DISCONNECTED " + request.host + ":" + request.port);
        } catch (Throwable t) {
            logInfo("VLESS DISCONNECTED");
            FileLog.e(t);
        } finally {
            closeQuietly(extraCloseable);
            closeQuietly(upstreamSocket);
            closeQuietly(clientSocket);
        }
    }

    private UpstreamChannel openUpstream(@NonNull String host, int port) throws Exception {
        Socket socket = new Socket(config.server, config.port);
        socket.setTcpNoDelay(true);

        if (config.tlsEnabled) {
            logInfo("VLESS TLS START " + config.server + ":" + config.port);
            socket = upgradeTls(socket);
        }

        if (config.transport == VlessProxyConfig.TRANSPORT_WS) {
            WebSocketTunnel tunnel = WebSocketTunnel.open(socket, config);
            tunnel.writeBinaryFrame(buildVlessRequest(host, port));
            logInfo("VLESS HANDSHAKE OK");
            return new UpstreamChannel(socket, tunnel.getInputStream(), tunnel.getOutputStream(), tunnel);
        }

        OutputStream outputStream = new BufferedOutputStream(socket.getOutputStream());
        outputStream.write(buildVlessRequest(host, port));
        outputStream.flush();

        InputStream inputStream = new VlessResponseInputStream(new BufferedInputStream(socket.getInputStream()));
        logInfo("VLESS HANDSHAKE OK");
        return new UpstreamChannel(socket, inputStream, outputStream, null);
    }

    private Socket upgradeTls(@NonNull Socket socket) throws IOException, NoSuchAlgorithmException, KeyManagementException {
        String hostName = !TextUtils.isEmpty(config.sni) ? config.sni : config.server;
        SSLSocketFactory sslSocketFactory = config.insecure ? insecureSslContext().getSocketFactory() : (SSLSocketFactory) SSLSocketFactory.getDefault();
        SSLSocket sslSocket = (SSLSocket) sslSocketFactory.createSocket(socket, config.server, config.port, true);
        sslSocket.startHandshake();
        if (!config.insecure) {
            HostnameVerifier verifier = HttpsURLConnection.getDefaultHostnameVerifier();
            SSLSession session = sslSocket.getSession();
            if (!verifier.verify(hostName, session)) {
                throw new IOException("TLS hostname verification failed");
            }
        }
        return sslSocket;
    }

    private static SSLContext insecureSslContext() throws NoSuchAlgorithmException, KeyManagementException {
        TrustManager[] trustManagers = new TrustManager[]{
                new X509TrustManager() {
                    @Override
                    public void checkClientTrusted(X509Certificate[] chain, String authType) {
                    }

                    @Override
                    public void checkServerTrusted(X509Certificate[] chain, String authType) {
                    }

                    @Override
                    public X509Certificate[] getAcceptedIssuers() {
                        return new X509Certificate[0];
                    }
                }
        };
        SSLContext context = SSLContext.getInstance("TLS");
        context.init(null, trustManagers, new SecureRandom());
        return context;
    }

    private byte[] buildVlessRequest(@NonNull String host, int port) throws IOException {
        UUID uuid = UUID.fromString(config.uuid);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.write(0x00);

        ByteBuffer uuidBuffer = ByteBuffer.allocate(16);
        uuidBuffer.putLong(uuid.getMostSignificantBits());
        uuidBuffer.putLong(uuid.getLeastSignificantBits());
        out.write(uuidBuffer.array());

        out.write(0x00);
        out.write(CMD_CONNECT);
        out.write((port >> 8) & 0xff);
        out.write(port & 0xff);

        InetAddress address = null;
        try {
            address = InetAddress.getByName(host);
        } catch (Throwable ignore) {
        }

        if (address instanceof Inet4Address) {
            out.write(0x01);
            out.write(address.getAddress());
        } else if (address instanceof Inet6Address) {
            out.write(0x03);
            out.write(address.getAddress());
        } else {
            byte[] hostBytes = host.getBytes(StandardCharsets.UTF_8);
            out.write(0x02);
            out.write(hostBytes.length);
            out.write(hostBytes);
        }
        return out.toByteArray();
    }

    private static SocksRequest negotiateSocks(@NonNull DataInputStream input, @NonNull OutputStream output) throws IOException {
        int version = input.readUnsignedByte();
        if (version != 0x05) {
            throw new IOException("Unsupported SOCKS version");
        }
        int methodsCount = input.readUnsignedByte();
        boolean supportsNoAuth = false;
        for (int i = 0; i < methodsCount; i++) {
            if (input.readUnsignedByte() == 0x00) {
                supportsNoAuth = true;
            }
        }
        if (!supportsNoAuth) {
            output.write(new byte[]{0x05, (byte) 0xff});
            output.flush();
            throw new IOException("SOCKS auth method not supported");
        }
        output.write(new byte[]{0x05, 0x00});
        output.flush();

        int requestVersion = input.readUnsignedByte();
        int command = input.readUnsignedByte();
        input.readUnsignedByte();
        int atyp = input.readUnsignedByte();

        if (requestVersion != 0x05 || command != 0x01) {
            throw new IOException("Unsupported SOCKS command");
        }

        String host;
        switch (atyp) {
            case 0x01: {
                byte[] raw = new byte[4];
                input.readFully(raw);
                host = InetAddress.getByAddress(raw).getHostAddress();
                break;
            }
            case 0x03: {
                int length = input.readUnsignedByte();
                byte[] raw = new byte[length];
                input.readFully(raw);
                host = new String(raw, StandardCharsets.UTF_8);
                break;
            }
            case 0x04: {
                byte[] raw = new byte[16];
                input.readFully(raw);
                host = InetAddress.getByAddress(raw).getHostAddress();
                break;
            }
            default:
                throw new IOException("Unsupported SOCKS address type");
        }

        int port = input.readUnsignedShort();
        return new SocksRequest(host, port);
    }

    private static void writeSocksSuccess(@NonNull OutputStream output) throws IOException {
        output.write(new byte[]{0x05, 0x00, 0x00, 0x01, 0, 0, 0, 0, 0, 0});
        output.flush();
    }

    private static void pipeBothWays(@NonNull InputStream clientInput, @NonNull OutputStream clientOutput, @NonNull InputStream remoteInput, @NonNull OutputStream remoteOutput) throws InterruptedException {
        AtomicBoolean closed = new AtomicBoolean(false);
        CountDownLatch latch = new CountDownLatch(2);

        Thread uplink = new Thread(() -> {
            try {
                pipe(clientInput, remoteOutput, closed);
            } finally {
                closed.set(true);
                closeQuietly(remoteOutput);
                latch.countDown();
            }
        }, "vless-uplink");

        Thread downlink = new Thread(() -> {
            try {
                pipe(remoteInput, clientOutput, closed);
            } finally {
                closed.set(true);
                closeQuietly(clientOutput);
                latch.countDown();
            }
        }, "vless-downlink");

        uplink.start();
        downlink.start();
        latch.await();
    }

    private static void pipe(@NonNull InputStream input, @NonNull OutputStream output, @NonNull AtomicBoolean closed) {
        byte[] buffer = new byte[8192];
        try {
            while (!closed.get()) {
                int read = input.read(buffer);
                if (read < 0) {
                    return;
                }
                if (read > 0) {
                    output.write(buffer, 0, read);
                    output.flush();
                }
            }
        } catch (Throwable ignore) {
        }
    }

    private static void closeQuietly(Closeable closeable) {
        if (closeable != null) {
            try {
                closeable.close();
            } catch (Throwable ignore) {
            }
        }
    }

    private static void logInfo(@NonNull String message) {
        FileLog.d(message);
    }

    private static final class SocksRequest {
        private final String host;
        private final int port;

        private SocksRequest(String host, int port) {
            this.host = host;
            this.port = port;
        }
    }

    private static final class UpstreamChannel {
        private final Socket socket;
        private final InputStream inputStream;
        private final OutputStream outputStream;
        private final Closeable extraCloseable;

        private UpstreamChannel(Socket socket, InputStream inputStream, OutputStream outputStream, Closeable extraCloseable) {
            this.socket = socket;
            this.inputStream = inputStream;
            this.outputStream = outputStream;
            this.extraCloseable = extraCloseable;
        }
    }

    private static final class VlessResponseInputStream extends InputStream {
        private final InputStream inputStream;
        private boolean headerConsumed;

        private VlessResponseInputStream(@NonNull InputStream inputStream) {
            this.inputStream = inputStream;
        }

        @Override
        public int read() throws IOException {
            byte[] buffer = new byte[1];
            int read = read(buffer, 0, 1);
            return read < 0 ? -1 : (buffer[0] & 0xff);
        }

        @Override
        public int read(@NonNull byte[] buffer, int off, int len) throws IOException {
            if (len == 0) {
                return 0;
            }
            ensureHeaderConsumed();
            return inputStream.read(buffer, off, len);
        }

        private void ensureHeaderConsumed() throws IOException {
            if (headerConsumed) {
                return;
            }
            int version = inputStream.read();
            if (version < 0) {
                throw new EOFException("Missing VLESS response version");
            }
            int addonLength = inputStream.read();
            if (addonLength < 0) {
                throw new EOFException("Missing VLESS response addon length");
            }
            for (int i = 0; i < addonLength; i++) {
                if (inputStream.read() < 0) {
                    throw new EOFException("Unexpected end of VLESS response");
                }
            }
            headerConsumed = true;
        }
    }

    private static final class WebSocketTunnel implements Closeable {
        private static final int OPCODE_BINARY = 0x2;
        private static final int OPCODE_CLOSE = 0x8;

        private final Socket socket;
        private final InputStream input;
        private final OutputStream output;
        private final Queue<byte[]> frames = new ArrayDeque<>();
        private final Object frameLock = new Object();
        private boolean vlessHeaderConsumed;
        private boolean closed;

        private WebSocketTunnel(Socket socket) throws IOException {
            this.socket = socket;
            this.input = new BufferedInputStream(socket.getInputStream());
            this.output = new BufferedOutputStream(socket.getOutputStream());
        }

        public static WebSocketTunnel open(@NonNull Socket socket, @NonNull VlessProxyConfig config) throws IOException {
            WebSocketTunnel tunnel = new WebSocketTunnel(socket);
            tunnel.handshake(config);
            return tunnel;
        }

        private void handshake(@NonNull VlessProxyConfig config) throws IOException {
            byte[] nonce = new byte[16];
            new SecureRandom().nextBytes(nonce);
            String key = android.util.Base64.encodeToString(nonce, android.util.Base64.NO_WRAP);
            String host = !TextUtils.isEmpty(config.sni) ? config.sni : config.server;
            String path = VlessProxyConfig.normalizeWsPath(config.wsPath);

            String request =
                    "GET " + path + " HTTP/1.1\r\n" +
                            "Host: " + host + "\r\n" +
                            "Upgrade: websocket\r\n" +
                            "Connection: Upgrade\r\n" +
                            "Sec-WebSocket-Key: " + key + "\r\n" +
                            "Sec-WebSocket-Version: 13\r\n\r\n";
            output.write(request.getBytes(StandardCharsets.UTF_8));
            output.flush();

            String response = readHttpHeaders(input);
            if (!response.startsWith("HTTP/1.1 101") && !response.startsWith("HTTP/1.0 101")) {
                throw new IOException("WebSocket handshake failed");
            }
        }

        public void writeBinaryFrame(@NonNull byte[] payload) throws IOException {
            writeFrame(OPCODE_BINARY, payload);
        }

        public InputStream getInputStream() {
            return new InputStream() {
                private byte[] current;
                private int offset;

                @Override
                public int read() throws IOException {
                    byte[] one = new byte[1];
                    int read = read(one, 0, 1);
                    return read < 0 ? -1 : (one[0] & 0xff);
                }

                @Override
                public int read(@NonNull byte[] buffer, int off, int len) throws IOException {
                    if (len == 0) {
                        return 0;
                    }
                    while (current == null || offset >= current.length) {
                        synchronized (frameLock) {
                            if (!frames.isEmpty()) {
                                current = frames.remove();
                                offset = 0;
                                break;
                            }
                        }
                        current = readNextPayloadMessage();
                        offset = 0;
                        if (current.length == 0) {
                            continue;
                        }
                    }

                    int count = Math.min(len, current.length - offset);
                    System.arraycopy(current, offset, buffer, off, count);
                    offset += count;
                    return count;
                }
            };
        }

        public OutputStream getOutputStream() {
            return new OutputStream() {
                @Override
                public void write(int b) throws IOException {
                    write(new byte[]{(byte) b});
                }

                @Override
                public void write(@NonNull byte[] b, int off, int len) throws IOException {
                    byte[] payload = new byte[len];
                    System.arraycopy(b, off, payload, 0, len);
                    writeBinaryFrame(payload);
                }

                @Override
                public void flush() throws IOException {
                    output.flush();
                }
            };
        }

        private byte[] readNextBinaryMessage() throws IOException {
            while (true) {
                int first = input.read();
                if (first < 0) {
                    throw new EOFException("WebSocket closed");
                }
                int second = input.read();
                if (second < 0) {
                    throw new EOFException("WebSocket closed");
                }

                int opcode = first & 0x0f;
                boolean masked = (second & 0x80) != 0;
                long length = second & 0x7f;
                if (length == 126) {
                    length = ((input.read() & 0xff) << 8) | (input.read() & 0xff);
                } else if (length == 127) {
                    byte[] raw = new byte[8];
                    readFully(input, raw);
                    length = ByteBuffer.wrap(raw).getLong();
                }

                byte[] mask = null;
                if (masked) {
                    mask = new byte[4];
                    readFully(input, mask);
                }

                byte[] payload = new byte[(int) length];
                readFully(input, payload);
                if (masked && mask != null) {
                    for (int i = 0; i < payload.length; i++) {
                        payload[i] ^= mask[i % 4];
                    }
                }

                if (opcode == OPCODE_CLOSE) {
                    throw new EOFException("WebSocket close frame");
                }
                if (opcode == OPCODE_BINARY) {
                    return payload;
                }
            }
        }

        private byte[] readNextPayloadMessage() throws IOException {
            while (true) {
                byte[] payload = readNextBinaryMessage();
                if (!vlessHeaderConsumed) {
                    if (payload.length < 2) {
                        throw new EOFException("Short VLESS WS response");
                    }
                    int addonLength = payload[1] & 0xff;
                    int payloadOffset = 2 + addonLength;
                    if (payloadOffset > payload.length) {
                        throw new EOFException("Invalid VLESS WS response");
                    }
                    vlessHeaderConsumed = true;
                    if (payloadOffset == payload.length) {
                        continue;
                    }
                    byte[] stripped = new byte[payload.length - payloadOffset];
                    System.arraycopy(payload, payloadOffset, stripped, 0, stripped.length);
                    return stripped;
                }
                return payload;
            }
        }

        private void writeFrame(int opcode, @NonNull byte[] payload) throws IOException {
            output.write(0x80 | opcode);
            int length = payload.length;
            if (length < 126) {
                output.write(0x80 | length);
            } else if (length <= 0xffff) {
                output.write(0x80 | 126);
                output.write((length >> 8) & 0xff);
                output.write(length & 0xff);
            } else {
                output.write(0x80 | 127);
                output.write(ByteBuffer.allocate(8).putLong(length).array());
            }
            byte[] mask = new byte[4];
            new SecureRandom().nextBytes(mask);
            output.write(mask);

            byte[] masked = new byte[payload.length];
            for (int i = 0; i < payload.length; i++) {
                masked[i] = (byte) (payload[i] ^ mask[i % 4]);
            }
            output.write(masked);
            output.flush();
        }

        private static String readHttpHeaders(@NonNull InputStream inputStream) throws IOException {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            int matched = 0;
            byte[] end = new byte[]{'\r', '\n', '\r', '\n'};
            while (matched < end.length) {
                int value = inputStream.read();
                if (value < 0) {
                    throw new EOFException("Unexpected end of stream");
                }
                out.write(value);
                matched = value == end[matched] ? matched + 1 : (value == end[0] ? 1 : 0);
            }
            return out.toString(StandardCharsets.UTF_8.name());
        }

        private static void readFully(@NonNull InputStream inputStream, @NonNull byte[] buffer) throws IOException {
            int offset = 0;
            while (offset < buffer.length) {
                int read = inputStream.read(buffer, offset, buffer.length - offset);
                if (read < 0) {
                    throw new EOFException("Unexpected end of stream");
                }
                offset += read;
            }
        }

        @Override
        public void close() throws IOException {
            if (!closed) {
                closed = true;
                socket.close();
            }
        }
    }
}
