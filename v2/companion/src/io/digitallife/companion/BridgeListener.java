package io.digitallife.companion;

import android.service.notification.*;
import android.app.Notification;
import android.os.SystemClock;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.*;
import org.json.*;

public class BridgeListener extends NotificationListenerService {
    private ServerSocket server;
    private volatile boolean ready=false;
    private long activeUntil=0;
    private final Set<String> packages=new HashSet<>();
    private final LinkedHashMap<String,JSONObject> queue=new LinkedHashMap<>();
    @Override public void onCreate() {
        super.onCreate();
        new Thread(() -> {
            try {
                server=new ServerSocket();server.bind(new InetSocketAddress("127.0.0.1",8766));
                while (!server.isClosed()) { try(Socket socket=server.accept()) { serve(socket); } catch(Exception ignored) {} }
            } catch(IOException ignored) {}
        },"digitallife-local-bridge").start();
    }
    @Override public void onListenerConnected() { ready=true; }
    @Override public synchronized void onListenerDisconnected() {ready=false;clear();}
    @Override public synchronized void onDestroy() { clear();try {if(server!=null)server.close();}catch(IOException ignored){} super.onDestroy(); }
    private void clear(){activeUntil=0;queue.clear();packages.clear();}
    private boolean active(){if(SystemClock.elapsedRealtime()>=activeUntil){clear();return false;}return ready;}
    static String sha(String value) throws Exception {
        byte[] digest=MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
        StringBuilder out=new StringBuilder();for(byte b:digest)out.append(String.format("%02x",b));return out.toString();
    }
    static String clip(Object value,int max) {String s=value==null?"":value.toString();return s.substring(0,Math.min(max,s.length()));}
    @Override public synchronized void onNotificationPosted(StatusBarNotification sbn) {
        if(!active() || !packages.contains(sbn.getPackageName()))return;
        Notification n=sbn.getNotification();
        if((n.flags&(Notification.FLAG_ONGOING_EVENT|Notification.FLAG_GROUP_SUMMARY))!=0)return;
        try {
            String title=clip(n.extras.getCharSequence(Notification.EXTRA_TITLE),100);
            CharSequence big=n.extras.getCharSequence(Notification.EXTRA_BIG_TEXT);
            String text=clip(big==null?n.extras.getCharSequence(Notification.EXTRA_TEXT):big,300);
            String key=sha(sbn.getKey()+"|"+sbn.getPostTime()+"|"+n.when+"|"+title+"|"+text);
            JSONObject event=new JSONObject().put("package",sbn.getPackageName()).put("event_key",key)
                .put("posted_at",sbn.getPostTime()/1000.0).put("age_seconds",Math.max(0,System.currentTimeMillis()-sbn.getPostTime())/1000.0)
                .put("_queued_elapsed",SystemClock.elapsedRealtime())
                .put("title",title).put("text",text);
            if(queue.size()>=100 && !queue.containsKey(key))queue.remove(queue.keySet().iterator().next());
            queue.put(key,event);
        }catch(Exception ignored){}
    }
    private void serve(Socket socket) throws Exception {
        socket.setSoTimeout(2000);
        InputStream in=socket.getInputStream();
        String first=line(in); if(first==null)return;
        String[] request=first.split(" ");if(request.length!=3)return;
        Map<String,String> headers=new HashMap<>();
        for(int i=0;i<32;i++){String h=line(in);if(h==null)return;if(h.isEmpty())break;int split=h.indexOf(':');if(split>0)headers.put(h.substring(0,split).toLowerCase(Locale.ROOT),h.substring(split+1).trim());}
        String token="";
        try{token=new String(Files.readAllBytes(new File(getFilesDir(),"bridge.token").toPath()),StandardCharsets.US_ASCII).trim();}catch(IOException ignored){}
        String auth=headers.getOrDefault("authorization","");
        if(!token.matches("[a-f0-9]{64}") || !MessageDigest.isEqual(("Bearer "+token).getBytes(StandardCharsets.US_ASCII),auth.getBytes(StandardCharsets.US_ASCII))){respond(socket,401,new JSONObject().put("error","unauthorized"));return;}
        int length=Integer.parseInt(headers.getOrDefault("content-length","0"));if(length<0 || length>16384)return;
        byte[] body=new byte[length];int read=0;while(read<length){int n=in.read(body,read,length-read);if(n<0)return;read+=n;}
        JSONObject data=length==0?new JSONObject():new JSONObject(new String(body,StandardCharsets.UTF_8));
        JSONObject response=new JSONObject();int status=200;
        synchronized(this){
            active();
            String route=request[0]+" "+request[1];
            switch(route){
            case "GET /status":response.put("listener_ready",ready).put("active",activeUntil>0).put("queued",queue.size());break;
            case "POST /session":
                clear();int ttl=data.getInt("ttl_seconds");if(ttl<1||ttl>600)throw new IllegalArgumentException();
                JSONArray allowed=data.getJSONArray("packages");if(allowed.length()>20)throw new IllegalArgumentException();
                for(int i=0;i<allowed.length();i++){String p=allowed.getString(i);if(!p.matches("[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*)+"))throw new IllegalArgumentException();packages.add(p);}
                activeUntil=SystemClock.elapsedRealtime()+ttl*1000L;
                new android.os.Handler(getMainLooper()).postDelayed(() -> {synchronized(BridgeListener.this){if(SystemClock.elapsedRealtime()>=activeUntil)clear();}},ttl*1000L);
                response.put("active",ready).put("ttl_seconds",ttl);break;
            case "DELETE /session":clear();response.put("active",false);break;
            case "GET /events":JSONArray events=new JSONArray();if(active())for(JSONObject e:queue.values()){
                JSONObject copy=new JSONObject(e.toString());
                copy.put("age_seconds",e.getDouble("age_seconds")+(SystemClock.elapsedRealtime()-e.getLong("_queued_elapsed"))/1000.0);
                copy.remove("_queued_elapsed");events.put(copy);
            }response.put("events",events);break;
            case "POST /ack":JSONArray keys=data.getJSONArray("keys");for(int i=0;i<Math.min(100,keys.length());i++)queue.remove(keys.getString(i));response.put("remaining",queue.size());break;
            case "POST /test_notification":if(!active()){status=409;response.put("error","inactive");}else{MainActivity.postTest(this);response.put("posted",true);}break;
            default:status=404;response.put("error","not_found");
            }
        }
        respond(socket,status,response);
    }
    private String line(InputStream in)throws IOException{
        ByteArrayOutputStream bytes=new ByteArrayOutputStream();int c;
        while((c=in.read())!=-1){if(c=='\n')return new String(bytes.toByteArray(),StandardCharsets.US_ASCII).replace("\r","");bytes.write(c);if(bytes.size()>8192)throw new IOException();}return null;
    }
    private void respond(Socket socket,int status,JSONObject data)throws IOException{
        byte[] body=data.toString().getBytes(StandardCharsets.UTF_8);
        OutputStream out=socket.getOutputStream();out.write(("HTTP/1.1 "+status+" Result\r\nContent-Type: application/json\r\nContent-Length: "+body.length+"\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));out.write(body);out.flush();
    }
}
