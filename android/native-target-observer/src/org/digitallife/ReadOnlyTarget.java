package org.digitallife;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.UiAutomation;
import android.graphics.Point;
import android.graphics.Rect;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.SystemClock;
import android.view.Display;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONObject;

/** OFFLINE PROTOTYPE. Not installed, connected to a worker, or an input capability.
 * One fixed Settings row, two bounded native samples, no UI strings in output.
 */
public final class ReadOnlyTarget {
    private static final String PACKAGE = "com.android.settings";
    // Native child indices from the private approved XML, not serialized positions.
    // A changed path/title is a failure, never a search/fallback or a coordinate.
    private static final int[] ROW = {0,0,0,0,0,4,0,0,10};
    private static final String ROW_PATH = "0/0/0/0/0/4/0/0/10";
    private static final String TITLE_PATH = ROW_PATH + "/1";
    private static final String TITLE = "显示与亮度";

    private static String hash(String value) throws Exception {
        byte[] data = MessageDigest.getInstance("SHA-256").digest(value.getBytes("UTF-8"));
        StringBuilder result = new StringBuilder();
        for (byte b : data) result.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
        return result.toString();
    }
    private static void require(boolean ok) {
        if (!ok) throw new IllegalStateException("TARGET_OBSERVATION_REJECTED");
    }
    private static String str(CharSequence value) { return value == null ? "" : value.toString(); }
    private static JSONObject properties(AccessibilityNodeInfo node) throws Exception {
        Rect rect = new Rect(); node.getBoundsInScreen(rect);
        return new JSONObject().put("bounds", new JSONArray(new int[]{rect.left,rect.top,rect.right,rect.bottom}))
            .put("visible",node.isVisibleToUser()).put("editable",node.isEditable())
            .put("clickable",node.isClickable()).put("enabled",node.isEnabled())
            .put("checkable",node.isCheckable()).put("password",node.isPassword())
            .put("long_clickable",node.isLongClickable());
    }
    private static JSONObject sample(UiAutomation ui) throws Exception {
        long started = SystemClock.elapsedRealtimeNanos();
        List<AccessibilityWindowInfo> windows = ui.getWindows();
        List<AccessibilityNodeInfo> held = new ArrayList<>();
        require(!windows.isEmpty() && windows.size() <= 20);
        try {
            AccessibilityWindowInfo chosen = null;
            for (AccessibilityWindowInfo w : windows) {
                if (w.getType() == AccessibilityWindowInfo.TYPE_APPLICATION && w.isActive() && w.isFocused()) {
                    require(chosen == null); chosen = w;
                }
            }
            require(chosen != null);
            AccessibilityNodeInfo root = ui.getRootInActiveWindow();
            require(root != null); held.add(root);
            int windowId = chosen.getId();
            AccessibilityNodeInfo current = root;
            boolean visible = true;
            for (int step = 0; step <= ROW.length; step++) {
                require(current.refresh());
                require(PACKAGE.equals(str(current.getPackageName())) && current.getWindowId() == windowId);
                visible &= current.isVisibleToUser();
                require(!current.isPassword());
                if (step == ROW.length) break;
                AccessibilityNodeInfo child = current.getChild(ROW[step]);
                require(child != null); held.add(child); current = child;
            }
            require("android.widget.LinearLayout".equals(str(current.getClassName())));
            JSONObject row = properties(current);
            AccessibilityNodeInfo title = current.getChild(1);
            require(title != null); held.add(title); require(title.refresh());
            require(PACKAGE.equals(str(title.getPackageName())) && title.getWindowId() == windowId);
            require("android.widget.TextView".equals(str(title.getClassName())));
            require("android:id/title".equals(title.getViewIdResourceName()));
            // Never read passwords/editors, other applications, arbitrary text,
            // content descriptions, hints, notifications or a complete UI tree.
            require(!title.isPassword() && !title.isEditable());
            require(TITLE.equals(str(title.getText())));
            Class<?> dm = Class.forName("android.hardware.display.DisplayManagerGlobal");
            Object manager = dm.getMethod("getInstance").invoke(null);
            Display display = (Display)dm.getMethod("getRealDisplay",int.class).invoke(manager,0);
            require(display != null);
            Point size = new Point(); display.getRealSize(size);
            Rect windowBounds = new Rect(); chosen.getBoundsInScreen(windowBounds);
            JSONObject result = new JSONObject().put("sample_started_ns",started)
                .put("window_id",windowId).put("window_layer",chosen.getLayer())
                .put("window_bounds",new JSONArray(new int[]{windowBounds.left,windowBounds.top,windowBounds.right,windowBounds.bottom}))
                .put("window_active",chosen.isActive()).put("window_focused",chosen.isFocused())
                .put("package",PACKAGE).put("width",size.x).put("height",size.y).put("rotation",display.getRotation())
                .put("ancestry_visible",visible).put("same_window",true).put("refreshed",true)
                .put("row",row).put("title",properties(title));
            // Bound every field acquisition, including the final node getters.
            result.put("sample_finished_ns",SystemClock.elapsedRealtimeNanos());
            return result;
        } finally {
            for (AccessibilityNodeInfo node : held) node.recycle();
            for (AccessibilityWindowInfo window : windows) window.recycle();
        }
    }
    public static void main(String[] args) {
        if (args.length != 1 || !args[0].matches("[a-f0-9]{32}")) {
            System.err.println("TARGET_OBSERVER_ERROR=INVALID_NONCE"); System.exit(2);
        }
        Thread watchdog = new Thread(() -> {
            try { Thread.sleep(4500); } catch (InterruptedException ignored) { return; }
            System.err.println("TARGET_OBSERVER_ERROR=REMOTE_DEADLINE"); Runtime.getRuntime().halt(4);
        },"target-observer-deadline");
        watchdog.setDaemon(true); watchdog.start();
        HandlerThread thread = new HandlerThread("target-observer");
        UiAutomation ui = null; JSONObject result = null; boolean connected = false; int exit = 0;
        try {
            Looper.prepareMainLooper(); thread.start();
            Class<?> connectionType = Class.forName("android.app.IUiAutomationConnection");
            Object connection = Class.forName("android.app.UiAutomationConnection").getConstructor().newInstance();
            ui = (UiAutomation)UiAutomation.class.getConstructor(Looper.class,connectionType).newInstance(thread.getLooper(),connection);
            // No fallback to a suppressing connection on an incompatible OEM.
            UiAutomation.class.getMethod("connectWithTimeout",int.class,long.class)
                .invoke(ui,UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES,1200L);
            connected = true;
            AccessibilityServiceInfo info = ui.getServiceInfo();
            require(info != null);
            info.flags |= AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
                | AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS | AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS;
            ui.setServiceInfo(info);
            JSONArray frames = new JSONArray(); frames.put(sample(ui)); frames.put(sample(ui));
            result = new JSONObject().put("schema","find-x6-target-observer/1").put("nonce",args[0])
                .put("selector_sha256",hash(PACKAGE+"|android:id/title|"+TITLE+"|"+ROW_PATH+"|"+TITLE_PATH))
                .put("row_path",ROW_PATH).put("title_path",TITLE_PATH).put("frames",frames);
        } catch (Throwable error) {
            // Do not print exception messages: OEM exceptions can contain UI data.
            System.err.println("TARGET_OBSERVER_ERROR=READ_FAILED"); exit = 2;
        } finally {
            if (ui != null) try { UiAutomation.class.getMethod("disconnect").invoke(ui); }
            catch (Throwable error) { exit = 3; }
            thread.quitSafely();
            try { if (thread.isAlive()) thread.join(300); }
            catch (InterruptedException error) { exit = 3; }
            if (thread.isAlive()) exit = 3;
        }
        if (exit == 0 && connected && result != null) {
            try { result.put("disconnected",true); System.out.println("DIGITALLIFE_TARGET="+result.toString()); }
            catch (Exception error) { exit = 3; }
        }
        System.exit(exit);
    }
}
