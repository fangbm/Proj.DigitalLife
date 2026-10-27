package io.digitallife.companion;

import android.app.*;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.*;
import android.media.projection.*;
import android.os.*;
import android.util.Base64;
import java.io.*;
import java.nio.*;
import java.util.UUID;
import org.json.*;

public class AudioCaptureService extends Service {
    private static volatile AudioCaptureService current;
    private MediaProjection projection;
    private volatile AudioRecord recorder;
    private volatile boolean running;
    private volatile String state="idle",id="",source="",reason="";
    private volatile byte[] wav;
    private volatile double rms;
    private Thread worker;
    @Override public IBinder onBind(Intent intent){return null;}
    @Override public int onStartCommand(Intent intent,int flags,int startId){
        try{
            NotificationManager manager=getSystemService(NotificationManager.class);
            manager.createNotificationChannel(new NotificationChannel("capture","播放音频采集",NotificationManager.IMPORTANCE_LOW));
            Notification n=new Notification.Builder(this,"capture").setSmallIcon(android.R.drawable.ic_btn_speak_now).setContentTitle("DigitalLife 有限会话音频授权")
              .setContentText("仅采集指定应用允许捕获的媒体音频；会话结束释放").setOngoing(true).build();
            startForeground(7301,n,ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
            if(intent==null || !BridgeListener.sessionActive()){stopForeground(STOP_FOREGROUND_REMOVE);stopSelf();return START_NOT_STICKY;}
            projection=getSystemService(MediaProjectionManager.class).getMediaProjection(intent.getIntExtra("result_code",0),intent.getParcelableExtra("projection_data"));
            projection.registerCallback(new MediaProjection.Callback(){@Override public void onStop(){if(current==AudioCaptureService.this)stopAll();}},new Handler(getMainLooper()));
            current=this;
        }catch(Exception error){reason="projection_unavailable";stopSelf();}
        return START_NOT_STICKY;
    }
    public static boolean available(){return current!=null && current.projection!=null;}
    public static synchronized JSONObject startCapture(String source,int seconds)throws Exception{
        AudioCaptureService s=current;
        if(s==null || s.projection==null)return new JSONObject().put("available",false).put("reason","projection_not_authorized");
        if(s.running)return new JSONObject().put("available",false).put("reason","capture_busy");
        if(seconds<1||seconds>20)throw new IllegalArgumentException();
        int uid=s.getPackageManager().getApplicationInfo(source,0).uid;
        AudioPlaybackCaptureConfiguration capture=new AudioPlaybackCaptureConfiguration.Builder(s.projection)
          .addMatchingUsage(AudioAttributes.USAGE_MEDIA).addMatchingUsage(AudioAttributes.USAGE_GAME).addMatchingUid(uid).build();
        AudioFormat format=new AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT).setSampleRate(16000).setChannelMask(AudioFormat.CHANNEL_IN_MONO).build();
        int buffer=Math.max(8192,AudioRecord.getMinBufferSize(16000,AudioFormat.CHANNEL_IN_MONO,AudioFormat.ENCODING_PCM_16BIT));
        s.recorder=new AudioRecord.Builder().setAudioFormat(format).setBufferSizeInBytes(buffer).setAudioPlaybackCaptureConfig(capture).build();
        s.id=UUID.randomUUID().toString();s.source=source;s.wav=null;s.reason="";s.state="recording";s.running=true;
        s.worker=new Thread(()->s.record(seconds),"digitallife-bounded-playback-audio");s.worker.start();
        return new JSONObject().put("available",true).put("capture_id",s.id).put("source_package",source).put("source_uid",uid).put("seconds",seconds);
    }
    private void record(int seconds){
        ByteArrayOutputStream pcm=new ByteArrayOutputStream();long squares=0,count=0;AudioRecord record=recorder;
        try{
            record.startRecording();long until=SystemClock.elapsedRealtime()+seconds*1000L;short[] buffer=new short[2048];
            while(running && SystemClock.elapsedRealtime()<until && pcm.size()<640000){
                int got=record.read(buffer,0,Math.min(buffer.length,(640000-pcm.size())/2));if(got<=0)break;
                for(int i=0;i<got;i++){int value=buffer[i];pcm.write(value&255);pcm.write((value>>8)&255);squares+=(long)value*value;count++;}
            }
            rms=count==0?0:Math.sqrt((double)squares/count);
            if(count<8000 || rms<8){state="unavailable";reason="silent_or_capture_disallowed";}
            else{byte[] data=pcm.toByteArray();ByteBuffer out=ByteBuffer.allocate(44+data.length).order(ByteOrder.LITTLE_ENDIAN);
                out.put(new byte[]{'R','I','F','F'}).putInt(36+data.length).put(new byte[]{'W','A','V','E','f','m','t',' '}).putInt(16).putShort((short)1).putShort((short)1).putInt(16000).putInt(32000).putShort((short)2).putShort((short)16).put(new byte[]{'d','a','t','a'}).putInt(data.length).put(data);wav=out.array();state="ready";}
        }catch(Exception error){state="unavailable";reason="capture_failed";}
        finally{running=false;try{record.stop();}catch(Exception ignored){}record.release();recorder=null;}
    }
    public static synchronized JSONObject result(String captureId,boolean finish)throws Exception{
        AudioCaptureService s=current;
        if(s==null)return new JSONObject().put("state","unavailable").put("reason","projection_not_authorized");
        if(!s.id.equals(captureId))return new JSONObject().put("state","unavailable").put("reason","capture_id_mismatch");
        if(finish && s.running){s.running=false;try{s.recorder.stop();}catch(Exception ignored){}if(s.worker!=null)s.worker.join(2000);}
        JSONObject result=new JSONObject().put("capture_id",s.id).put("source_package",s.source).put("state",s.state).put("reason",s.reason).put("rms",s.rms);
        if(finish && "ready".equals(s.state) && s.wav!=null){result.put("wav_base64",Base64.encodeToString(s.wav,Base64.NO_WRAP));s.wav=null;s.state="consumed";}
        return result;
    }
    public static synchronized void stopAll(){
        AudioCaptureService s=current;current=null;if(s==null)return;
        s.running=false;try{if(s.recorder!=null)s.recorder.stop();}catch(Exception ignored){}
        try{if(s.worker!=null)s.worker.join(2000);}catch(InterruptedException ignored){}
        s.wav=null;try{if(s.projection!=null)s.projection.stop();}catch(Exception ignored){}s.projection=null;s.stopForeground(STOP_FOREGROUND_REMOVE);s.stopSelf();
    }
    @Override public void onDestroy(){if(current==this)stopAll();super.onDestroy();}
}
