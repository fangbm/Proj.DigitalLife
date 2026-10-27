package io.digitallife.companion;
import android.app.Activity;
import android.os.Bundle;
import android.widget.*;
import java.io.File;
import java.nio.file.Files;
import java.nio.charset.StandardCharsets;
import org.json.*;

public class ReaderActivity extends Activity {
    private JSONObject article; private int page=0;
    @Override public void onCreate(Bundle state){
        super.onCreate(state);
        try{article=new JSONObject(new String(Files.readAllBytes(new File(getFilesDir(),"article.json").toPath()),StandardCharsets.UTF_8));showPage();}
        catch(Exception error){TextView text=new TextView(this);text.setText("未提供 article.json 阅读验收材料");setContentView(text);}
    }
    private void showPage(){
        try{
            JSONArray pages=article.getJSONArray("pages");
            LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);box.setPadding(30,100,30,30);
            TextView title=new TextView(this);title.setId(android.R.id.title);title.setText(article.getString("title"));title.setTextSize(24);box.addView(title);
            TextView position=new TextView(this);position.setText("第 "+(page+1)+" 页 / "+pages.length()+" 页");box.addView(position);
            LinearLayout buttons=new LinearLayout(this);
            Button previous=new Button(this);previous.setText("上一页");previous.setEnabled(page>0);previous.setOnClickListener(v->{page--;showPage();});buttons.addView(previous);
            Button next=new Button(this);next.setText("下一页");next.setEnabled(page+1<pages.length());next.setOnClickListener(v->{page++;showPage();});buttons.addView(next);box.addView(buttons);
            ScrollView scroll=new ScrollView(this);TextView body=new TextView(this);body.setId(android.R.id.message);body.setTextSize(18);
            JSONArray paragraphs=pages.getJSONArray(page);StringBuilder content=new StringBuilder();
            for(int i=0;i<paragraphs.length();i++)content.append(paragraphs.getString(i)).append("\n\n");
            body.setText(content.toString());scroll.addView(body);box.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));setContentView(box);
        }catch(Exception error){finish();}
    }
}
