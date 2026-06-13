package org.telegram.proxy.vless;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;

import androidx.annotation.Nullable;

import org.telegram.messenger.ApplicationLoader;
import org.telegram.messenger.R;
import org.telegram.ui.LaunchActivity;

public class VlessForegroundService extends Service {
    private static final String ACTION_START = "org.telegram.proxy.vless.START";
    private static final String ACTION_STOP = "org.telegram.proxy.vless.STOP";
    private static final String CHANNEL_ID = "vless_proxy_service";
    private static final int NOTIFICATION_ID = 94430;

    public static void start(Context context) {
        Intent intent = new Intent(context, VlessForegroundService.class);
        intent.setAction(ACTION_START);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(intent);
        } else {
            context.startService(intent);
        }
    }

    public static void stop(Context context) {
        context.stopService(new Intent(context, VlessForegroundService.class));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent != null ? intent.getAction() : null;
        if (ACTION_STOP.equals(action) || !VlessProxyManager.getInstance().hasActiveTunnels()) {
            stopForeground(STOP_FOREGROUND_REMOVE);
            stopSelf();
            return START_NOT_STICKY;
        }

        createChannel();
        startForeground(NOTIFICATION_ID, buildNotification());
        return START_STICKY;
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private Notification buildNotification() {
        Context context = ApplicationLoader.applicationContext != null ? ApplicationLoader.applicationContext : this;
        Intent launchIntent = new Intent(context, LaunchActivity.class);
        launchIntent.setAction(Intent.ACTION_MAIN);
        launchIntent.addCategory(Intent.CATEGORY_LAUNCHER);
        launchIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pendingIntent = PendingIntent.getActivity(
                context,
                0,
                launchIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M ? PendingIntent.FLAG_IMMUTABLE : 0)
        );

        return new Notification.Builder(this, CHANNEL_ID)
                .setContentTitle(getApplicationInfo().loadLabel(getPackageManager()))
                .setContentText("VLESS proxy active")
                .setSmallIcon(R.drawable.notification)
                .setContentIntent(pendingIntent)
                .setOngoing(true)
                .setShowWhen(false)
                .build();
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null || manager.getNotificationChannel(CHANNEL_ID) != null) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "VLESS connection",
                NotificationManager.IMPORTANCE_MIN
        );
        channel.setShowBadge(false);
        channel.setSound(null, null);
        manager.createNotificationChannel(channel);
    }
}
