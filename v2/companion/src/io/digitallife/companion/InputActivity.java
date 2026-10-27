package io.digitallife.companion;
import android.app.Activity;
import android.os.Bundle;
import android.text.InputType;
import android.widget.*;

/** Local-only editor for literal Unicode input acceptance, no network or send. */
public class InputActivity extends Activity {
    @Override public void onCreate(Bundle state){
        super.onCreate(state);
        LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);box.setPadding(40,100,40,40);
        TextView title=new TextView(this);title.setText("中文输入验收：仅本机编辑框，不外发");title.setTextSize(20);box.addView(title);
        EditText input=new EditText(this);input.setId(android.R.id.edit);input.setContentDescription("本机文字输入验收");
        input.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_FLAG_MULTI_LINE);box.addView(input);
        setContentView(box);
    }
}
