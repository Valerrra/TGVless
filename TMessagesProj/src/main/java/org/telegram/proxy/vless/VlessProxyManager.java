package org.telegram.proxy.vless;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import org.telegram.messenger.FileLog;
import org.telegram.messenger.SharedConfig;

import java.net.ServerSocket;
import java.util.HashMap;
import java.util.Map;

public class VlessProxyManager {
    public static final class ResolvedProxy {
        public final String address;
        public final int port;
        public final String username;
        public final String password;
        public final String secret;

        private ResolvedProxy(String address, int port, String username, String password, String secret) {
            this.address = address;
            this.port = port;
            this.username = username;
            this.password = password;
            this.secret = secret;
        }
    }

    private static final VlessProxyManager INSTANCE = new VlessProxyManager();

    public static VlessProxyManager getInstance() {
        return INSTANCE;
    }

    private final Object lock = new Object();
    private final Map<String, VlessTunnel> tunnels = new HashMap<>();

    public @Nullable ResolvedProxy resolve(@Nullable SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return null;
        }
        if (!proxyInfo.isVless()) {
            return new ResolvedProxy(proxyInfo.address, proxyInfo.port, proxyInfo.username, proxyInfo.password, proxyInfo.secret);
        }
        synchronized (lock) {
            try {
                VlessProxyConfig config = proxyInfo.getVlessConfig();
                config.validate(false);
                String key = proxyInfo.getVlessKey();
                VlessTunnel tunnel = tunnels.get(key);
                if (tunnel == null) {
                    int localPort = allocateLocalPort();
                    tunnel = new VlessTunnel(config.withLocalPort(localPort));
                    tunnel.start();
                    tunnels.put(key, tunnel);
                }
                return new ResolvedProxy("127.0.0.1", tunnel.getLocalPort(), "", "", "");
            } catch (Throwable t) {
                FileLog.e("Unable to start VLESS tunnel");
                FileLog.e(t);
                return null;
            }
        }
    }

    public void stopAllExcept(@Nullable SharedConfig.ProxyInfo proxyInfo) {
        String keepKey = proxyInfo != null && proxyInfo.isVless() ? proxyInfo.getVlessKey() : null;
        synchronized (lock) {
            String[] keys = tunnels.keySet().toArray(new String[0]);
            for (String key : keys) {
                if (keepKey == null || !keepKey.equals(key)) {
                    VlessTunnel tunnel = tunnels.remove(key);
                    if (tunnel != null) {
                        tunnel.close();
                    }
                }
            }
        }
    }

    public void stopAll() {
        stopAllExcept(null);
    }

    private static int allocateLocalPort() throws Exception {
        for (int port = 23000; port <= 23999; port++) {
            try (ServerSocket ignored = new ServerSocket(port)) {
                return port;
            } catch (Throwable ignore) {
            }
        }
        throw new IllegalStateException("No free local VLESS port");
    }
}
