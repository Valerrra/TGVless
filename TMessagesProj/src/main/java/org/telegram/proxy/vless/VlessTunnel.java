package org.telegram.proxy.vless;

import androidx.annotation.NonNull;

import java.io.Closeable;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

public class VlessTunnel implements Closeable {
    private final VlessProxyConfig config;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private final ExecutorService executor = Executors.newCachedThreadPool();

    private ServerSocket serverSocket;
    private Thread acceptThread;

    public VlessTunnel(@NonNull VlessProxyConfig config) {
        this.config = config;
    }

    public synchronized void start() throws IOException {
        if (running.get()) {
            return;
        }
        serverSocket = new ServerSocket();
        serverSocket.bind(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), config.localPort));
        running.set(true);
        acceptThread = new Thread(this::acceptLoop, "vless-socks-" + config.localPort);
        acceptThread.start();
    }

    private void acceptLoop() {
        while (running.get()) {
            try {
                Socket client = serverSocket.accept();
                executor.execute(new VlessConnection(client, config));
            } catch (Throwable t) {
                if (running.get()) {
                    org.telegram.messenger.FileLog.e("VLESS accept failed");
                    org.telegram.messenger.FileLog.e(t);
                }
            }
        }
    }

    public int getLocalPort() {
        return config.localPort;
    }

    @Override
    public synchronized void close() {
        running.set(false);
        if (serverSocket != null) {
            try {
                serverSocket.close();
            } catch (Throwable ignore) {
            }
            serverSocket = null;
        }
        if (acceptThread != null) {
            acceptThread.interrupt();
            acceptThread = null;
        }
        executor.shutdownNow();
    }
}
