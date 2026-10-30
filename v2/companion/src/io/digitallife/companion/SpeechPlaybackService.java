package io.digitallife.companion;

import android.app.*;
import android.content.Intent;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.media.AudioManager;
import android.os.*;
import org.json.*;

/** Visible media-playback lease, started from our foreground activity only. */
public class SpeechPlaybackService extends Service {
    private static volatile SpeechPlaybackService current;
    private AudioManager audio;
    private int original=-1,actual=-1;
    private boolean muted;
    @Override public IBinder onBind(Intent intent){return null;}
    @Override public int onStartCommand(Intent intent,int flags,int id){
        if(!BridgeListener.sessionActive()){stopSelf();return START_NOT_STICKY;}
        NotificationManager notifications=getSystemService(NotificationManager.class);
        notifications.createNotificationChannel(new NotificationChannel("speech_playback","有限会话语音播放",NotificationManager.IMPORTANCE_LOW));
        Notification notification=new Notification.Builder(this,"speech_playback").setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("DigitalLife 语音播放测试").setContentText("只播放测试音频，会话结束停止并恢复音量").setOngoing(true).build();
        startForeground(7302,notification,ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
        if(current==this)return START_NOT_STICKY;
        restorePending(this);
        audio=getSystemService(AudioManager.class);original=audio.getStreamVolume(AudioManager.STREAM_MUSIC);muted=audio.isStreamMute(AudioManager.STREAM_MUSIC);
        SharedPreferences journal=getSharedPreferences("m6_playback_restore",MODE_PRIVATE);
        if(!journal.contains("volume"))journal.edit().putInt("volume",original).putBoolean("muted",muted).commit();
        audio.adjustStreamVolume(AudioManager.STREAM_MUSIC,AudioManager.ADJUST_UNMUTE,0);
        audio.setStreamVolume(AudioManager.STREAM_MUSIC,Math.max(1,audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC)/2),0);
        actual=audio.getStreamVolume(AudioManager.STREAM_MUSIC);current=this;
        Handler handler=new Handler(getMainLooper());handler.postDelayed(new Runnable(){public void run(){
            if(current!=SpeechPlaybackService.this)return;
            if(!BridgeListener.sessionActive()){stopAll();return;}
            handler.postDelayed(this,500);
        }},500);
        return START_NOT_STICKY;
    }
    public static JSONObject status(Context context)throws Exception{
        SpeechPlaybackService service=current;
        return new JSONObject().put("active",service!=null).put("original_volume",service==null?-1:service.original)
            .put("actual_playback_volume",service==null?-1:service.actual)
            .put("volume_restore_pending",context.getSharedPreferences("m6_playback_restore",MODE_PRIVATE).contains("volume"));
    }
    public static synchronized void stopAll(){
        SpeechPlaybackService service=current;current=null;if(service==null)return;
        SpeechAudio.stop();
        restorePending(service);
        service.stopForeground(STOP_FOREGROUND_REMOVE);service.stopSelf();
    }
    @Override public void onDestroy(){if(current==this)stopAll();super.onDestroy();}
    public static synchronized boolean restorePending(Context context){
        if(current!=null)return false;
        SharedPreferences journal=context.getSharedPreferences("m6_playback_restore",MODE_PRIVATE);
        if(!journal.contains("volume"))return true;
        AudioManager audio=(AudioManager)context.getSystemService(Context.AUDIO_SERVICE);
        int wanted=journal.getInt("volume",0);boolean mute=journal.getBoolean("muted",false);
        audio.adjustStreamVolume(AudioManager.STREAM_MUSIC,mute?AudioManager.ADJUST_MUTE:AudioManager.ADJUST_UNMUTE,0);
        audio.setStreamVolume(AudioManager.STREAM_MUSIC,wanted,0);
        boolean restored=audio.getStreamVolume(AudioManager.STREAM_MUSIC)==wanted && (wanted==0 || audio.isStreamMute(AudioManager.STREAM_MUSIC)==mute);
        if(restored)journal.edit().clear().commit();
        return restored;
    }
}
