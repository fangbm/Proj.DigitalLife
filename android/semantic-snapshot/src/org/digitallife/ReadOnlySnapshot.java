package org.digitallife;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.UiAutomation;
import android.graphics.Point;
import android.graphics.Rect;
import android.os.SystemClock;
import android.view.Display;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.List;

/** Shell-only, bounded read-only UI snapshot. No input, shell execution, settings or grant API. */
public final class ReadOnlySnapshot {
    private static int count;
    private static final int MAX_NODES = 1800;
    private static String str(CharSequence value) { return value == null ? "" : value.toString(); }
    private static JSONArray bounds(Rect rect) throws Exception {
        return new JSONArray(new int[]{rect.left, rect.top, rect.right, rect.bottom});
    }
    private static JSONObject node(AccessibilityNodeInfo n, String path, int depth) throws Exception {
        if (depth > 60 || ++count > MAX_NODES) throw new IllegalStateException("NODE_LIMIT");
        Rect rect = new Rect(); n.getBoundsInScreen(rect);
        JSONObject obj = new JSONObject();
        obj.put("path", path); obj.put("bounds", bounds(rect));
        obj.put("text", str(n.getText())); obj.put("content_description", str(n.getContentDescription()));
        obj.put("class_name", str(n.getClassName())); obj.put("resource_id", str(n.getViewIdResourceName()));
        obj.put("package", str(n.getPackageName())); obj.put("clickable", n.isClickable());
        obj.put("scrollable", n.isScrollable()); obj.put("enabled", n.isEnabled());
        obj.put("checkable", n.isCheckable()); obj.put("editable", n.isEditable());
        obj.put("visible", n.isVisibleToUser()); obj.put("password", n.isPassword());
        obj.put("long_clickable", n.isLongClickable());
        JSONArray children = new JSONArray();
        for (int i = 0; i < n.getChildCount(); i++) {
            AccessibilityNodeInfo child = n.getChild(i);
            if (child == null) throw new IllegalStateException("MISSING_CHILD");
            try { children.put(node(child, path + "/" + i, depth + 1)); }
            finally { child.recycle(); }
        }
        obj.put("children", children); return obj;
    }
    public static void main(String[] args) {
        // Local adb cancellation can leave a remote process alive. Bound this read-only process
        // independently so a stuck Binder call cannot retain its UIAutomation connection forever.
        Thread watchdog = new Thread(() -> {
            try { Thread.sleep(4500); }
            catch (InterruptedException ignored) { return; }
            System.err.println("DIGITALLIFE_SNAPSHOT_ERROR=REMOTE_DEADLINE");
            Runtime.getRuntime().halt(4);
        }, "snapshot-deadline");
        watchdog.setDaemon(true); watchdog.start();
        Object wrapper = null; Class<?> type = null; int exit = 0;
        try {
            if (args.length != 1 || !args[0].matches("[a-f0-9]{32}")) throw new IllegalArgumentException("NONCE");
            type = Class.forName("com.android.uiautomator.core.UiAutomationShellWrapper");
            wrapper = type.getConstructor().newInstance(); type.getMethod("connect").invoke(wrapper);
            UiAutomation ui = (UiAutomation)type.getMethod("getUiAutomation").invoke(wrapper);
            AccessibilityServiceInfo service = ui.getServiceInfo();
            service.flags |= AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
                    | AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
                    | AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS;
            ui.setServiceInfo(service);
            SystemClock.sleep(180); // Bounded connection setup, deliberately no waitForIdle.
            Class<?> dm = Class.forName("android.hardware.display.DisplayManagerGlobal");
            Object manager = dm.getMethod("getInstance").invoke(null);
            Display display = (Display)dm.getMethod("getRealDisplay", int.class).invoke(manager, 0);
            Point size = new Point(); display.getRealSize(size);
            JSONObject result = new JSONObject();
            result.put("schema", "shell-ui-snapshot/1"); result.put("nonce", args[0]);
            result.put("rotation", display.getRotation()); result.put("width", size.x); result.put("height", size.y);
            JSONArray windows = new JSONArray(); List<AccessibilityWindowInfo> all = ui.getWindows();
            if (all.isEmpty() || all.size() > 20) throw new IllegalStateException("WINDOWS_UNAVAILABLE");
            for (AccessibilityWindowInfo w : all) {
                JSONObject window = new JSONObject(); Rect rect = new Rect(); w.getBoundsInScreen(rect);
                window.put("id", w.getId()); window.put("type", w.getType()); window.put("layer", w.getLayer());
                window.put("active", w.isActive()); window.put("focused", w.isFocused());
                window.put("title", str(w.getTitle())); window.put("bounds", bounds(rect));
                AccessibilityNodeInfo root = w.getRoot();
                if (root == null) throw new IllegalStateException("WINDOW_ROOT_UNAVAILABLE");
                try { window.put("root", node(root, "w" + w.getId(), 0)); }
                finally { root.recycle(); }
                windows.put(window);
            }
            result.put("windows", windows); result.put("node_count", count);
            System.out.println("DIGITALLIFE_SNAPSHOT=" + result.toString());
        } catch (Throwable error) {
            System.err.println("DIGITALLIFE_SNAPSHOT_ERROR=" + error.getClass().getSimpleName() + ":" + error.getMessage());
            exit = 2;
        } finally {
            if (wrapper != null) try { type.getMethod("disconnect").invoke(wrapper); }
            catch (Throwable ignored) { exit = 3; }
        }
        System.exit(exit);
    }
}
