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
        text.setText("DigitalLife M3 感知桥\n\n仅在桌面有限会话内提供通知与经系统授权的播放音频。\n认证令牌由 ADB 写入私有文件。\n不发送聊天或评论，不使用麦克风兜底。\n");
        text.setTextSize(20); box.addView(text);
        Button test = new Button(this); test.setText("发送本机测试通知"); test.setOnClickListener(v -> postTest(this)); box.addView(test);
        Button video = new Button(this); video.setText("播放公开视频验收片段"); video.setOnClickListener(v -> startActivity(new Intent(this,VideoActivity.class))); box.addView(video);
        Button article = new Button(this); article.setText("阅读长文验收材料"); article.setOnClickListener(v -> startActivity(new Intent(this,ReaderActivity.class))); box.addView(article);
        Button audio = new Button(this); audio.setText("授权采集应用播放音频");
        audio.setOnClickListener(v -> {
            if(android.os.Build.VERSION.SDK_INT<29 || !BridgeListener.sessionActive()){android.widget.Toast.makeText(this,"请先启动桌面有限会话",android.widget.Toast.LENGTH_LONG).show();return;}
            android.media.projection.MediaProjectionManager manager=getSystemService(android.media.projection.MediaProjectionManager.class);
            Intent intent=android.os.Build.VERSION.SDK_INT>=34?manager.createScreenCaptureIntent(android.media.projection.MediaProjectionConfig.createConfigForDefaultDisplay()):manager.createScreenCaptureIntent();
            startActivityForResult(intent,73);
        });box.addView(audio);
        Button speech = new Button(this); speech.setText("播放语音验收材料"); speech.setOnClickListener(v -> startActivity(new Intent(this,SpeechActivity.class))); box.addView(speech);
        Button input = new Button(this); input.setText("测试中文输入"); input.setOnClickListener(v -> startActivity(new Intent(this,InputActivity.class))); box.addView(input);
        Button settings = new Button(this); settings.setText("打开系统通知监听设置");
        settings.setOnClickListener(v -> startActivity(new Intent("android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS"))); box.addView(settings);
        setContentView(box);
        NotificationListenerService.requestRebind(new ComponentName(this,BridgeListener.class));
    }
    @Override protected void onActivityResult(int request,int result,Intent data){
        super.onActivityResult(request,result,data);
        if(request==73 && result==RESULT_OK && data!=null){
            if(!BridgeListener.sessionActive())return;
            Intent service=new Intent(this,AudioCaptureService.class).putExtra("result_code",result).putExtra("projection_data",data);
            startForegroundService(service);
        }
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
