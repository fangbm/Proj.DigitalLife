package io.digitallife.companion;

import android.app.Activity;
import android.os.Bundle;
import android.content.Intent;

/** Explicit Android share target. Stores one in-memory public-link receipt. */
public class ShareCaptureActivity extends Activity {
    @Override public void onCreate(Bundle state){
        super.onCreate(state);
        Intent intent=getIntent();
        if(Intent.ACTION_SEND.equals(intent.getAction()) && "text/plain".equals(intent.getType())){
            android.net.Uri referrer=getReferrer();
            BridgeListener.acceptPublicShare(intent.getStringExtra(Intent.EXTRA_TEXT),
                intent.getStringExtra(Intent.EXTRA_SUBJECT),referrer==null?"":referrer.toString());
        }
        finish();
    }
}
