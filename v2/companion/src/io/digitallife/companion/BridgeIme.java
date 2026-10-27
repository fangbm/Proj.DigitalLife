package io.digitallife.companion;

import android.inputmethodservice.InputMethodService;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;
import android.provider.Settings;
import java.util.LinkedHashSet;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.json.JSONObject;

/** Literal Unicode insertion into an empty focused editor. Never presses send. */
public class BridgeIme extends InputMethodService {
    private static volatile BridgeIme current;
    private static volatile long generation;
    private final LinkedHashSet<String> used = new LinkedHashSet<>();
    @Override public void onCreate(){super.onCreate();current=this;}
    @Override public void onDestroy(){if(current==this)current=null;super.onDestroy();}
    @Override public boolean onEvaluateInputViewShown(){return false;}
    public static void invalidate(){generation++;}
    public static boolean available(){return current!=null;}
    private boolean selected(){
        String value=Settings.Secure.getString(getContentResolver(),Settings.Secure.DEFAULT_INPUT_METHOD);
        return (getPackageName()+"/.BridgeIme").equals(value) || (getPackageName()+"/"+getClass().getName()).equals(value);
    }
    public static JSONObject inspect(String target,String requestId)throws Exception{
        BridgeIme ime=current;long epoch=generation;
        if(ime==null || !requestId.matches("[a-f0-9]{32}"))return new JSONObject().put("available",false);
        CountDownLatch done=new CountDownLatch(1);JSONObject result=new JSONObject();
        new Handler(Looper.getMainLooper()).post(()->{
            try{
                EditorInfo info=ime.getCurrentInputEditorInfo();InputConnection input=ime.getCurrentInputConnection();
                if(current!=ime || epoch!=generation || !BridgeListener.sessionActive() || !ime.selected() || info==null || input==null || !target.equals(info.packageName) || (info.inputType&InputType.TYPE_MASK_CLASS)!=InputType.TYPE_CLASS_TEXT){result.put("available",false);return;}
                int variant=info.inputType&InputType.TYPE_MASK_VARIATION;
                if(variant==InputType.TYPE_TEXT_VARIATION_PASSWORD || variant==InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD || variant==InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD){result.put("available",false);return;}
                CharSequence before=input.getTextBeforeCursor(601,0),after=input.getTextAfterCursor(601,0),selection=input.getSelectedText(0);
                if(before==null || after==null || before.length()+after.length()+(selection==null?0:selection.length())>600){result.put("available",false);return;}
                result.put("available",true).put("request_id",requestId).put("package",info.packageName).put("field_id",info.fieldId).put("input_type",info.inputType).put("text",before.toString()+(selection==null?"":selection.toString())+after.toString());
            }catch(Exception error){try{result.put("available",false);}catch(Exception ignored){}}
            finally{done.countDown();}
        });
        if(!done.await(1500,TimeUnit.MILLISECONDS))return new JSONObject().put("available",false);
        return result;
    }
    public static JSONObject insert(String requestId,String target,String text)throws Exception{
        BridgeIme ime=current;long epoch=generation;
        if(ime==null || !BridgeListener.sessionActive())return new JSONObject().put("committed",false).put("reason","ime_or_session_unavailable");
        if(!requestId.matches("[a-f0-9]{32}") || text.length()==0 || text.length()>600 || text.codePointCount(0,text.length())>300 || text.codePoints().anyMatch(c->c<32 || c>=0xd800&&c<=0xdfff))throw new IllegalArgumentException();
        synchronized(ime.used){if(!ime.used.add(requestId))return new JSONObject().put("committed",false).put("reason","request_already_attempted");while(ime.used.size()>100)ime.used.remove(ime.used.iterator().next());}
        CountDownLatch done=new CountDownLatch(1);JSONObject result=new JSONObject();
        new Handler(Looper.getMainLooper()).post(()->{
            try{
                if(current!=ime || epoch!=generation || !BridgeListener.sessionActive() || !ime.selected()){result.put("committed",false).put("reason","session_or_ime_changed");return;}
                EditorInfo info=ime.getCurrentInputEditorInfo();InputConnection input=ime.getCurrentInputConnection();
                if(info==null || input==null || !target.equals(info.packageName) || (info.inputType&InputType.TYPE_MASK_CLASS)!=InputType.TYPE_CLASS_TEXT){result.put("committed",false).put("reason","focused_editor_unverified");return;}
                int variant=info.inputType&InputType.TYPE_MASK_VARIATION;
                if(variant==InputType.TYPE_TEXT_VARIATION_PASSWORD || variant==InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD || variant==InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD){result.put("committed",false).put("reason","password_editor");return;}
                CharSequence before=input.getTextBeforeCursor(1,0),after=input.getTextAfterCursor(1,0),selection=input.getSelectedText(0);
                if(before==null || after==null || before.length()!=0 || after.length()!=0 || selection!=null&&selection.length()!=0){result.put("committed",false).put("reason","existing_draft_or_unknown_editor");return;}
                result.put("committed",input.commitText(text,1)).put("request_id",requestId);
            }catch(Exception error){try{result.put("uncertain",true);}catch(Exception ignored){}}
            finally{done.countDown();}
        });
        if(!done.await(1500,TimeUnit.MILLISECONDS))return new JSONObject().put("uncertain",true).put("request_id",requestId);
        return result;
    }
}
