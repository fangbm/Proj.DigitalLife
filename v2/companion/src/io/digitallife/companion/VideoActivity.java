package io.digitallife.companion;
import android.app.Activity;
import android.os.Bundle;
import android.widget.*;
import android.net.Uri;
import java.io.File;

public class VideoActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        File source = new File(getFilesDir(),"acceptance.mp4");
        if (!source.exists()) { TextView message=new TextView(this);message.setText("尚未提供验收视频 acceptance.mp4");setContentView(message);return; }
        LinearLayout box=new LinearLayout(this); box.setOrientation(LinearLayout.VERTICAL);
        TextView title=new TextView(this); title.setText("公开视频验收片段 · 来源见桌面 source.json · 无音频采集");box.addView(title);
        VideoView video=new VideoView(this);box.addView(video,new LinearLayout.LayoutParams(-1,-1));
        setContentView(box);video.setVideoURI(Uri.fromFile(source));
        video.setOnPreparedListener(player -> {player.setLooping(true);player.setVolume(0,0);video.start();});
    }
}
