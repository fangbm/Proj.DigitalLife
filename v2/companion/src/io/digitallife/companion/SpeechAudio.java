package io.digitallife.companion;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.media.AudioManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.speech.tts.Voice;
import java.io.File;
import java.nio.file.Files;
import java.util.*;
import org.json.*;

/** Own TTS and bounded playback only. Does not inject into another app's mic. */
public final class SpeechAudio {
    private static final Handler main=new Handler(Looper.getMainLooper());
    private static TextToSpeech tts;
    private static MediaPlayer player;
    private static String engine="",state="not_initialized",voice="",job="",error="";
    private static File file;
    private static long deadline;
    private static int duration;
    private static final Set<String> played=new HashSet<>();
    private static boolean offline;
    private static Context appContext;
    private static AudioManager audio;
    private static int savedVolume=-1;
    private static boolean savedMute;

    public static synchronized JSONObject status(boolean fetch)throws Exception {
        if(deadline>0 && SystemClock.elapsedRealtime()>deadline)stop();
        JSONObject out=new JSONObject().put("state",state).put("engine",engine).put("voice_id",voice)
            .put("offline",offline).put("job_id",job).put("error",error).put("duration_ms",duration)
            .put("microphone_injection",false).put("playback_is_delivery",false);
        if(fetch && file!=null && file.isFile() && "ready".equals(state)){
            if(file.length()>2000000)throw new IllegalArgumentException("speech_file_limit");
            out.put("wav_base64",android.util.Base64.encodeToString(Files.readAllBytes(file.toPath()),android.util.Base64.NO_WRAP));
        }
        return out;
    }
    public static synchronized JSONObject initialize(Context context)throws Exception {
        if(tts!=null)return status(false);
        state="initializing";
        Context app=context.getApplicationContext();
        appContext=app;
        main.post(() -> {
            synchronized(SpeechAudio.class){
                if(tts!=null)return;
                tts=new TextToSpeech(app,result -> main.post(() -> {
                    synchronized(SpeechAudio.class){
                        if(tts==null)return;
                        if(result!=TextToSpeech.SUCCESS){state="unavailable";error="tts_initialization_failed";return;}
                        engine=tts.getDefaultEngine();
                        Set<Voice> voices=tts.getVoices();
                        List<Voice> candidates=new ArrayList<>();
                        if(voices!=null)for(Voice v:voices)if(v.getLocale().getLanguage().equals("zh") && !v.isNetworkConnectionRequired())candidates.add(v);
                        candidates.sort(Comparator.comparing(Voice::getName));
                        if(candidates.isEmpty()){state="unavailable";error="offline_Chinese_voice_unavailable";return;}
                        Voice selected=candidates.get(0);
                        if(tts.setVoice(selected)!=TextToSpeech.SUCCESS){state="unavailable";error="voice_selection_failed";return;}
                        voice=selected.getName();offline=true;
                        tts.setSpeechRate(1f);tts.setPitch(1f);
                        tts.setOnUtteranceProgressListener(new UtteranceProgressListener(){
                            public void onStart(String id){}
                            public void onDone(String id){synchronized(SpeechAudio.class){if(id.equals(job) && "synthesizing".equals(state))state="ready";}}
                            @Deprecated public void onError(String id){synchronized(SpeechAudio.class){if(id.equals(job)){state="failed";error="synthesis_failed";}}}
                        });
                        state="idle";
                    }
                }));
            }
        });
        return status(false);
    }
    public static synchronized JSONObject synthesize(Context context,JSONObject data)throws Exception {
        if(tts==null || !offline || !(state.equals("idle") || state.equals("ready") || state.equals("played")))return status(false);
        String text=data.getString("text"),id=data.getString("job_id");
        if(!id.matches("[a-f0-9]{32}") || text.isEmpty() || text.length()>300 || text.contains("\u0000"))throw new IllegalArgumentException();
        File directory=new File(context.getFilesDir(),"m6-speech");directory.mkdirs();
        file=new File(directory,id+".wav");
        if(file.exists())throw new IllegalArgumentException("job_already_exists");
        job=id;state="synthesizing";error="";duration=0;deadline=SystemClock.elapsedRealtime()+120000;
        File target=file;
        main.post(() -> {synchronized(SpeechAudio.class){
            if(!id.equals(job) || !BridgeListener.sessionActive())return;
            if(tts.synthesizeToFile(text,new Bundle(),target,id)!=TextToSpeech.SUCCESS){state="failed";error="synthesis_enqueue_failed";}
        }});
        return status(false);
    }
    public static synchronized JSONObject play(JSONObject data)throws Exception {
        String id=data.getString("job_id");
        if(!id.equals(job) || !"ready".equals(state) || !played.add(id) || file==null)return new JSONObject().put("started",false).put("reason","not_ready_or_already_played");
        File target=file;state="starting_playback";
        main.post(() -> {synchronized(SpeechAudio.class){
            if(!BridgeListener.sessionActive() || !id.equals(job)){stop();return;}
            try{
                audio=(AudioManager)appContext.getSystemService(Context.AUDIO_SERVICE);
                savedVolume=audio.getStreamVolume(AudioManager.STREAM_MUSIC);savedMute=audio.isStreamMute(AudioManager.STREAM_MUSIC);
                audio.adjustStreamVolume(AudioManager.STREAM_MUSIC,AudioManager.ADJUST_UNMUTE,0);
                audio.setStreamVolume(AudioManager.STREAM_MUSIC,Math.max(1,audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC)/2),0);
                if(audio.getStreamVolume(AudioManager.STREAM_MUSIC)==0)throw new IllegalStateException("media_muted");
                player=new MediaPlayer();
                player.setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build());
                player.setDataSource(target.getAbsolutePath());player.prepare();duration=player.getDuration();
                if(duration<300 || duration>30000)throw new IllegalArgumentException("playback_duration_limit");
                player.setOnCompletionListener(p -> {synchronized(SpeechAudio.class){p.release();if(player==p)player=null;restoreVolume();state="played";}});
                player.start();state="playing";deadline=SystemClock.elapsedRealtime()+duration+3000;
            }catch(Exception failure){state="failed";error="playback_failed:"+failure.getClass().getSimpleName()+":"+String.valueOf(failure.getMessage());if(player!=null){player.release();player=null;}restoreVolume();}
        }});
        main.postDelayed(() -> {synchronized(SpeechAudio.class){if(id.equals(job) && (state.equals("playing") || state.equals("starting_playback")))stop();}},33000);
        return new JSONObject().put("started",true).put("job_id",id).put("microphone_injection",false);
    }
    public static synchronized JSONObject importAsset(Context context,JSONObject data)throws Exception {
        appContext=context.getApplicationContext();
        if(player!=null || state.equals("synthesizing"))throw new IllegalArgumentException("speech_busy");
        String id=data.getString("job_id"),expected=data.getString("sha256");
        if(!id.matches("[a-f0-9]{32}") || !expected.matches("[a-f0-9]{64}"))throw new IllegalArgumentException();
        byte[] raw=android.util.Base64.decode(data.getString("wav_base64"),android.util.Base64.DEFAULT);
        if(raw.length<44 || raw.length>650000 || raw[0]!='R' || raw[1]!='I' || raw[2]!='F' || raw[3]!='F'
                || raw[8]!='W' || raw[9]!='A' || raw[10]!='V' || raw[11]!='E')throw new IllegalArgumentException("wav_required");
        byte[] bytes=java.security.MessageDigest.getInstance("SHA-256").digest(raw);
        StringBuilder sum=new StringBuilder();for(byte b:bytes)sum.append(String.format("%02x",b));
        if(!sum.toString().equals(expected))throw new IllegalArgumentException("speech_hash_mismatch");
        File directory=new File(context.getFilesDir(),"m6-speech");directory.mkdirs();
        File target=new File(directory,id+".wav");if(target.exists())throw new IllegalArgumentException("job_already_exists");
        Files.write(target.toPath(),raw);file=target;job=id;state="ready";error="";duration=0;
        engine="imported_local_TTS_asset";voice=data.getString("voice_id");offline=true;deadline=SystemClock.elapsedRealtime()+120000;
        return status(false).put("sha256",expected).put("bytes",raw.length);
    }
    public static synchronized void stop(){
        if(player!=null){try{player.stop();}catch(Exception ignored){}player.release();player=null;}
        if(tts!=null)tts.stop();
        restoreVolume();
        if(file!=null){file.delete();file=null;}
        if(!state.equals("not_initialized") && !state.equals("unavailable"))state=offline?"idle":"not_initialized";
        deadline=0;job="";
    }
    private static void restoreVolume(){
        if(audio!=null && savedVolume>=0){audio.setStreamVolume(AudioManager.STREAM_MUSIC,savedVolume,0);if(savedMute)audio.adjustStreamVolume(AudioManager.STREAM_MUSIC,AudioManager.ADJUST_MUTE,0);savedVolume=-1;}
    }
}
