package io.digitallife.companion;
import android.app.Activity;
import android.os.Bundle;
import android.media.*;
import android.widget.TextView;
import java.io.File;

public class SpeechActivity extends Activity {
    private MediaPlayer player;
    @Override public void onCreate(Bundle state){
        super.onCreate(state);
        TextView text=new TextView(this);text.setText("播放语音验收材料\n来源与文件哈希见桌面证据\n没有屏幕字幕");text.setTextSize(24);text.setPadding(30,150,30,30);setContentView(text);
        try{
            player=new MediaPlayer();player.setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build());
            player.setDataSource(new File(getFilesDir(),"speech.wav").getAbsolutePath());player.setVolume(.25f,.25f);player.setLooping(true);player.prepare();player.start();
        }catch(Exception error){text.setText("语音材料不可用");}
    }
    @Override protected void onPause(){if(player!=null && player.isPlaying())player.pause();super.onPause();}
    @Override protected void onResume(){super.onResume();if(player!=null)player.start();}
    @Override protected void onDestroy(){if(player!=null){player.release();player=null;}super.onDestroy();}
}
