package org.digitallife;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.UiAutomation;
import android.graphics.Point;
import android.graphics.Rect;
import android.os.Build;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.SystemClock;
import android.view.Display;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.JSONArray;
import org.json.JSONObject;
import static org.digitallife.ObserverStages.Code.*;

/** Diagnostic only. Same shell bootstrap and fixed selector; no input capability. */
public final class ReadOnlyTarget {
    private static final String PACKAGE="com.android.settings";
    // Native indices, not filtered XML positions. No search or fallback.
    private static final int[] ROW={0,0,0,0,0,4,0,0,10};
    private static final String ROW_PATH="0/0/0/0/0/4/0/0/10", TITLE_PATH=ROW_PATH+"/1", TITLE="显示与亮度";
    private static String hash(String value) throws Exception {
        byte[] data=MessageDigest.getInstance("SHA-256").digest(value.getBytes("UTF-8"));
        StringBuilder result=new StringBuilder();
        for(byte b:data) result.append(String.format(java.util.Locale.ROOT,"%02x",b & 255));
        return result.toString();
    }
    private static void require(boolean ok, ObserverStages.Code code) throws ObserverStages.Rejected {
        if(!ok) throw new ObserverStages.Rejected(code);
    }
    private static String str(CharSequence value) {return value==null?"":value.toString();}
    private static JSONObject properties(AccessibilityNodeInfo node) throws Exception {
        Rect r=new Rect();node.getBoundsInScreen(r);
        return new JSONObject().put("bounds",rect(r)).put("visible",node.isVisibleToUser())
            .put("editable",node.isEditable()).put("clickable",node.isClickable()).put("enabled",node.isEnabled())
            .put("checkable",node.isCheckable()).put("password",node.isPassword()).put("long_clickable",node.isLongClickable());
    }
    private static JSONArray rect(Rect r) throws Exception {return new JSONArray(new int[]{r.left,r.top,r.right,r.bottom});}

    private static final class NativeBackend implements ObserverStages.Backend<JSONObject> {
        HandlerThread thread;UiAutomation ui;
        List<AccessibilityWindowInfo> windows=new ArrayList<>();
        final List<AccessibilityNodeInfo> held=new ArrayList<>();
        AccessibilityWindowInfo chosen;AccessibilityNodeInfo current,title;
        int windowId;boolean ancestryVisible;long started;
        public void environment() throws Exception {
            // Shell identity is necessary, not sufficient proof of OEM permission.
            require(Build.VERSION.SDK_INT==36 && android.os.Process.myUid()==2000,INIT_FAILED);
            Looper.prepareMainLooper();thread=new HandlerThread("target-observer");thread.start();
            require(Looper.getMainLooper()!=null && thread.getLooper()!=null,INIT_FAILED);
        }
        public void automation() throws Exception {
            Class<?> type=Class.forName("android.app.IUiAutomationConnection");
            Object connection=Class.forName("android.app.UiAutomationConnection").getConstructor().newInstance();
            ui=(UiAutomation)UiAutomation.class.getConstructor(Looper.class,type).newInstance(thread.getLooper(),connection);
            require(ui!=null,INIT_FAILED);
        }
        public void connect() throws Exception {
            // AOSP returns only after CONNECTED for this accessibility-using flag.
            // No suppressing fallback, permission grant or hidden-API exemption.
            UiAutomation.class.getMethod("connectWithTimeout",int.class,long.class)
                .invoke(ui,UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES,1200L);
        }
        public void configure() throws Exception {
            AccessibilityServiceInfo info=ui.getServiceInfo();require(info!=null,CONFIGURE_FAILED);
            info.flags|=AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
                |AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS|AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS;
            ui.setServiceInfo(info);
        }
        public void window() throws Exception {
            started=SystemClock.elapsedRealtimeNanos();windows=ui.getWindows();
            require(windows!=null && !windows.isEmpty() && windows.size()<=20,WINDOW_UNAVAILABLE);
            chosen=null;
            for(AccessibilityWindowInfo w:windows) {
                require(w!=null,WINDOW_UNAVAILABLE);
                if(w.getType()==AccessibilityWindowInfo.TYPE_APPLICATION && w.isActive() && w.isFocused()) {
                    require(chosen==null,WINDOW_AMBIGUOUS);chosen=w;
                }
            }
            require(chosen!=null,WINDOW_UNAVAILABLE);windowId=chosen.getId();
        }
        public void root() throws Exception {
            current=ui.getRootInActiveWindow();require(current!=null,ROOT_UNAVAILABLE);held.add(current);
        }
        public void target() throws Exception {
            ancestryVisible=true;
            for(int step=0;step<=ROW.length;step++) {
                require(current.refresh(),TARGET_REJECTED);
                require(PACKAGE.equals(str(current.getPackageName())) && current.getWindowId()==windowId,TARGET_REJECTED);
                ancestryVisible &= current.isVisibleToUser();require(!current.isPassword(),TARGET_REJECTED);
                if(step==ROW.length) break;
                current=current.getChild(ROW[step]);require(current!=null,TARGET_NOT_FOUND);held.add(current);
            }
            require("android.widget.LinearLayout".equals(str(current.getClassName())),TARGET_REJECTED);
            title=current.getChild(1);require(title!=null,TARGET_NOT_FOUND);held.add(title);
            require(title.refresh(),TARGET_REJECTED);
            require(PACKAGE.equals(str(title.getPackageName())) && title.getWindowId()==windowId,TARGET_REJECTED);
            require("android.widget.TextView".equals(str(title.getClassName())) && "android:id/title".equals(title.getViewIdResourceName()),TARGET_REJECTED);
            // Read only the compiled stock title after owner/password/editor checks.
            require(!title.isPassword() && !title.isEditable(),TARGET_REJECTED);
            require(TITLE.equals(str(title.getText())),TARGET_REJECTED);
        }
        public JSONObject attributes() throws Exception {
            Class<?> dm=Class.forName("android.hardware.display.DisplayManagerGlobal");
            Object manager=dm.getMethod("getInstance").invoke(null);
            Display display=(Display)dm.getMethod("getRealDisplay",int.class).invoke(manager,0);
            require(display!=null,ATTRIBUTE_UNAVAILABLE);
            Point size=new Point();display.getRealSize(size);Rect bounds=new Rect();chosen.getBoundsInScreen(bounds);
            JSONObject result=new JSONObject().put("sample_started_ns",started)
                .put("window_id",windowId).put("window_layer",chosen.getLayer()).put("window_bounds",rect(bounds))
                .put("window_active",chosen.isActive()).put("window_focused",chosen.isFocused())
                .put("package",PACKAGE).put("width",size.x).put("height",size.y).put("rotation",display.getRotation())
                .put("ancestry_visible",ancestryVisible).put("same_window",true).put("refreshed",true)
                .put("row",properties(current)).put("title",properties(title));
            result.put("sample_finished_ns",SystemClock.elapsedRealtimeNanos());return result;
        }
        public void release() throws Exception {
            // Attempt every recycle even if an earlier release throws.
            Throwable failure=null;
            for(AccessibilityNodeInfo node:held) try {node.recycle();} catch(Throwable e) {if(failure==null) failure=e;}
            held.clear();
            if(windows!=null) for(AccessibilityWindowInfo w:windows) if(w!=null) try {w.recycle();} catch(Throwable e) {if(failure==null) failure=e;}
            windows=new ArrayList<>();chosen=null;current=null;title=null;rethrow(failure);
        }
        public void cleanup() throws Exception {
            Throwable failure=null;
            try {release();} catch(Throwable e) {failure=e;}
            // Disconnect attempted after partial/failed connection, never assumed.
            if(ui!=null) try {UiAutomation.class.getMethod("disconnect").invoke(ui);} catch(Throwable e) {if(failure==null) failure=e;}
            if(thread!=null) try {
                thread.quitSafely();if(thread.isAlive()) thread.join(300);require(!thread.isAlive(),DISCONNECT_FAILED);
            } catch(Throwable e) {if(failure==null) failure=e;}
            rethrow(failure);
        }
        private static void rethrow(Throwable e) throws Exception {
            if(e instanceof Exception) throw (Exception)e;
            if(e instanceof Error) throw (Error)e;
        }
    }
    public static void main(String[] args) {
        if(args.length!=1 || !args[0].matches("[a-f0-9]{32}")) {
            System.out.println("DIGITALLIFE_OBSERVER={\"schema\":\"find-x6-observer-stage/2\",\"status\":\"FAILED\",\"code\":\"INVALID_NONCE\",\"stage\":\"PROCESS\",\"last_completed_stage\":\"PROCESS\",\"exception_type\":\"REJECTED\",\"duration_ns\":0,\"cleanup\":{\"status\":\"NOT_STARTED\",\"exception_type\":\"NONE\"}}");
            System.exit(2);return;
        }
        ObserverStages<JSONObject> runner=new ObserverStages<>(new NativeBackend(),SystemClock::elapsedRealtimeNanos);
        AtomicBoolean emitted=new AtomicBoolean();
        // Existing watchdog only. Cannot claim cleanup or target success.
        Thread watchdog=new Thread(()->{
            try {Thread.sleep(4500);} catch(InterruptedException ignored) {return;}
            if(emitted.compareAndSet(false,true)) System.out.println("DIGITALLIFE_OBSERVER="+runner.watchdogResult().failureJson());
            Runtime.getRuntime().halt(4);
        },"target-observer-deadline");watchdog.setDaemon(true);watchdog.start();
        ObserverStages.Outcome<JSONObject> outcome=runner.run();String wire;
        if(outcome.code==null) try {
            JSONObject packet=new JSONObject().put("schema","find-x6-target-observer/1").put("nonce",args[0])
                .put("selector_sha256",hash(PACKAGE+"|android:id/title|"+TITLE+"|"+ROW_PATH+"|"+TITLE_PATH))
                .put("row_path",ROW_PATH).put("title_path",TITLE_PATH).put("frames",new JSONArray(outcome.frames)).put("disconnected",true);
            wire=new JSONObject().put("schema","find-x6-observer-stage/2").put("status","SUCCESS")
                .put("stage","CLEANUP").put("duration_ns",outcome.durationNs)
                .put("cleanup",new JSONObject().put("status","OK").put("exception_type","NONE")).put("packet",packet).toString();
        } catch(Throwable e) {outcome=runner.outputFailure(e);wire=outcome.failureJson();}
        else wire=outcome.failureJson();
        if(emitted.compareAndSet(false,true)) System.out.println("DIGITALLIFE_OBSERVER="+wire);
        System.exit(outcome.code==null?0:outcome.status.equals("PARTIAL")?3:2);
    }
}
