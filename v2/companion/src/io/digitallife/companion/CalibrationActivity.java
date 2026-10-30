package io.digitallife.companion;

import android.app.Activity;
import android.os.Bundle;
import android.os.SystemClock;
import android.media.*;
import android.widget.*;
import java.io.ByteArrayOutputStream;
import java.nio.*;
import org.json.*;

/** Visible, finite own-app mic test; never sends a social message. */
public class CalibrationActivity extends Activity {
    private static volatile CalibrationActivity current;
    private volatile boolean running;
    private volatile AudioRecord recorder;
    private volatile byte[] wav;
    private volatile String state="idle",error="";
    private volatile String source="VOICE_COMMUNICATION";
    private AudioManager audio;
    private int savedVolume=-1,previousVolume=-1,recordingVolume=-1,maximumVolume=-1,inputDevice=-1;
    private boolean savedMute;
    private TextView label;
    @Override public void onCreate(Bundle value){
        super.onCreate(value);current=this;
        LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);box.setPadding(40,100,40,40);
        label=new TextView(this);label.setText("本机声音路径测试\n只录制本机测试短句，最长12秒，不向聊天发送。");label.setTextSize(22);box.addView(label);
        Button start=new Button(this);start.setText("开始本机声学测试");start.setOnClickListener(v -> start(MediaRecorder.AudioSource.VOICE_COMMUNICATION));box.addView(start);
        Button mic=new Button(this);mic.setText("普通麦克风声学测试");mic.setOnClickListener(v -> start(MediaRecorder.AudioSource.MIC));box.addView(mic);setContentView(box);
    }
    private void start(int audioSource){
        if(running || !BridgeListener.sessionActive())return;
        try{
            JSONObject speech=SpeechAudio.status(false);
            if(!speech.getString("state").equals("ready"))throw new IllegalStateException("speech_not_ready");
            final String id=speech.getString("job_id");
            audio=getSystemService(AudioManager.class);savedVolume=audio.getStreamVolume(AudioManager.STREAM_MUSIC);previousVolume=savedVolume;
            savedMute=audio.isStreamMute(AudioManager.STREAM_MUSIC);audio.adjustStreamVolume(AudioManager.STREAM_MUSIC,AudioManager.ADJUST_UNMUTE,0);
            maximumVolume=audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
            audio.setStreamVolume(AudioManager.STREAM_MUSIC,Math.max(1,maximumVolume/2),0);
            recordingVolume=audio.getStreamVolume(AudioManager.STREAM_MUSIC);
            if(recordingVolume==0)throw new IllegalStateException("media_volume_still_muted");
            int buffer=Math.max(8192,AudioRecord.getMinBufferSize(16000,AudioFormat.CHANNEL_IN_MONO,AudioFormat.ENCODING_PCM_16BIT));
            source=audioSource==MediaRecorder.AudioSource.MIC?"MIC":"VOICE_COMMUNICATION";
            recorder=new AudioRecord(audioSource,16000,AudioFormat.CHANNEL_IN_MONO,AudioFormat.ENCODING_PCM_16BIT,buffer);
            recorder.startRecording();running=true;state="recording";wav=null;label.setText("正在进行本机声学测试，最长12秒");
            AudioDeviceInfo routed=recorder.getRoutedDevice();inputDevice=routed==null?-1:routed.getType();
            new Thread(() -> record(id),"digitallife-m6-visible-calibration").start();
        }catch(Exception failure){restoreVolume();if(recorder!=null){recorder.release();recorder=null;}state="unavailable";error=failure.getClass().getSimpleName();label.setText("本机测试不可用："+error);}
    }
    private void record(String id){
        AudioRecord input=recorder;ByteArrayOutputStream pcm=new ByteArrayOutputStream();
        long started=SystemClock.elapsedRealtime();boolean played=false;
        try{
            short[] buffer=new short[1024];
            while(running && BridgeListener.sessionActive() && SystemClock.elapsedRealtime()-started<12000 && pcm.size()<400000){
                int n=input.read(buffer,0,Math.min(buffer.length,(400000-pcm.size())/2));if(n<=0)break;
                for(int i=0;i<n;i++){pcm.write(buffer[i]&255);pcm.write((buffer[i]>>8)&255);}
                if(!played && SystemClock.elapsedRealtime()-started>=700){played=true;SpeechAudio.play(new JSONObject().put("job_id",id));}
                if(played && SpeechAudio.status(false).optString("state").equals("played") && SystemClock.elapsedRealtime()-started>1500)break;
            }
            byte[] bytes=pcm.toByteArray();ByteBuffer out=ByteBuffer.allocate(44+bytes.length).order(ByteOrder.LITTLE_ENDIAN);
            out.put(new byte[]{'R','I','F','F'}).putInt(36+bytes.length).put(new byte[]{'W','A','V','E','f','m','t',' '}).putInt(16)
                .putShort((short)1).putShort((short)1).putInt(16000).putInt(32000).putShort((short)2).putShort((short)16)
                .put(new byte[]{'d','a','t','a'}).putInt(bytes.length).put(bytes);
            wav=running?out.array():null;state=running?"ready":"cancelled";
        }catch(Exception failure){state="unavailable";error=failure.getClass().getSimpleName();}
        finally{
            running=false;try{input.stop();}catch(Exception ignored){}input.release();recorder=null;
            restoreVolume();
            runOnUiThread(() -> label.setText("本机测试状态："+state+"\n未发送聊天消息。"));
        }
    }
    public static JSONObject result(boolean fetch)throws Exception{
        CalibrationActivity activity=current;
        if(activity==null)return new JSONObject().put("state","unavailable").put("reason","calibration_not_open");
        JSONObject out=new JSONObject().put("state",activity.state).put("error",activity.error)
            .put("audio_source",activity.source+"_own_app_visible_mic_calibration").put("social_messages_sent",0)
            .put("previous_music_volume",activity.previousVolume).put("actual_recording_music_volume",activity.recordingVolume)
            .put("maximum_music_volume",activity.maximumVolume).put("input_device_type",activity.inputDevice);
        byte[] raw=activity.wav;
        if(fetch && raw!=null && activity.state.equals("ready"))out.put("wav_base64",android.util.Base64.encodeToString(raw,android.util.Base64.NO_WRAP));
        return out;
    }
    public static void stopAll(){
        CalibrationActivity activity=current;if(activity==null)return;
        activity.running=false;AudioRecord input=activity.recorder;
        if(input!=null)try{input.stop();}catch(Exception ignored){}
        activity.restoreVolume();
        activity.wav=null;
    }
    private synchronized void restoreVolume(){if(audio!=null && savedVolume>=0){audio.setStreamVolume(AudioManager.STREAM_MUSIC,savedVolume,0);if(savedMute)audio.adjustStreamVolume(AudioManager.STREAM_MUSIC,AudioManager.ADJUST_MUTE,0);savedVolume=-1;}}
    @Override protected void onPause(){SpeechAudio.stop();stopAll();super.onPause();}
    @Override protected void onDestroy(){stopAll();if(current==this)current=null;super.onDestroy();}
}
