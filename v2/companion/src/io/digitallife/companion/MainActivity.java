package io.digitallife.companion;

import android.app.*;
import android.os.Bundle;
import android.content.Intent;
import android.content.ComponentName;
import android.service.notification.NotificationListenerService;
import android.widget.*;

public class MainActivity extends Activity {
    private static volatile boolean resumed;
    static boolean isForeground(){return resumed;}
    @Override protected void onResume(){super.onResume();resumed=true;SpeechPlaybackService.restorePending(this);}
    @Override protected void onPause(){resumed=false;super.onPause();}
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout box = new LinearLayout(this); box.setOrientation(LinearLayout.VERTICAL); box.setPadding(40,60,40,30);
        TextView text = new TextView(this);
        text.setText("DigitalLife M4 手机桥\n\n仅在桌面有限会话内提供通知、原文输入与经系统授权的播放音频。\n通知入口需要本页在前台，聊天发送由桌面独立复核。\n认证令牌由 ADB 写入私有文件，不在后台运行 AI。\n");
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
        Button calibration=new Button(this);calibration.setText("本机声音路径测试");calibration.setOnClickListener(v -> startActivity(new Intent(this,CalibrationActivity.class)));box.addView(calibration);
        Button playback=new Button(this);playback.setText("开启本机语音播放服务");playback.setOnClickListener(v -> {if(BridgeListener.sessionActive())startForegroundService(new Intent(this,SpeechPlaybackService.class));});box.addView(playback);
        Button stopPlayback=new Button(this);stopPlayback.setText("停止播放并恢复音量");stopPlayback.setOnClickListener(v -> {SpeechAudio.stop();SpeechPlaybackService.stopAll();SpeechPlaybackService.restorePending(this);});box.addView(stopPlayback);
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
