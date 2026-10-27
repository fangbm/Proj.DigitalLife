package io.digitallife.companion;

import android.app.*;
import android.os.Bundle;
import android.content.Intent;
import android.content.ComponentName;
import android.service.notification.NotificationListenerService;
import android.widget.*;

public class MainActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout box = new LinearLayout(this); box.setOrientation(LinearLayout.VERTICAL); box.setPadding(40,60,40,30);
        TextView text = new TextView(this);
        text.setText("DigitalLife M3 通知桥\n\n仅在桌面有限会话激活期间，将允许应用的通知转交本机。\n认证令牌仅由 ADB 写入私有文件。\n不发送聊天或评论，不采集音频。\n");
        text.setTextSize(20); box.addView(text);
        Button test = new Button(this); test.setText("发送本机测试通知"); test.setOnClickListener(v -> postTest(this)); box.addView(test);
        Button video = new Button(this); video.setText("播放公开视频验收片段"); video.setOnClickListener(v -> startActivity(new Intent(this,VideoActivity.class))); box.addView(video);
        Button settings = new Button(this); settings.setText("打开系统通知监听设置");
        settings.setOnClickListener(v -> startActivity(new Intent("android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS"))); box.addView(settings);
        setContentView(box);
        NotificationListenerService.requestRebind(new ComponentName(this,BridgeListener.class));
    }
    static void postTest(android.content.Context context) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel("acceptance","M3 本机验收",NotificationManager.IMPORTANCE_DEFAULT));
        Notification n = new Notification.Builder(context,"acceptance").setSmallIcon(android.R.drawable.ic_dialog_info)
          .setContentTitle("DigitalLife M3 通知验收").setContentText("本机系统测试通知：请只阅读，不发送消息。")
          .setAutoCancel(true).build();
        manager.notify((int)(android.os.SystemClock.uptimeMillis()%Integer.MAX_VALUE),n);
    }
}
