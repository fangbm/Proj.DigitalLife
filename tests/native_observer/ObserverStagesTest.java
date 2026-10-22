package org.digitallife;

import java.lang.reflect.InvocationTargetException;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.TimeoutException;
import static org.digitallife.ObserverStages.Code.*;
import static org.digitallife.ObserverStages.Stage.*;

/** Host JVM only: uses the production helper lifecycle, never Android SDK stubs. */
public final class ObserverStagesTest {
    static final String PRIVATE="secret-account@example.invalid /private/ui <hierarchy>secret</hierarchy>";
    static void check(boolean value) {if(!value) throw new AssertionError("OFFLINE_STAGE_ASSERTION");}
    static final class Fake implements ObserverStages.Backend<String>,ObserverStages.Clock {
        final Map<ObserverStages.Stage,Throwable> errors=new HashMap<>();
        final Map<ObserverStages.Stage,Integer> calls=new HashMap<>();
        long time=100;int samples;boolean nullAttributes,secondFailure,timeout;
        void call(ObserverStages.Stage stage) throws Exception {
            calls.put(stage,calls.getOrDefault(stage,0)+1);time+=100;
            if(timeout && stage==CONNECT) time=4_500_000_100L;
            Throwable e=errors.get(stage);
            if(e instanceof Exception) throw (Exception)e;
            if(e instanceof Error) throw (Error)e;
        }
        public long now(){return time;}
        public void environment() throws Exception {call(ENVIRONMENT);}
        public void automation() throws Exception {call(AUTOMATION);}
        public void connect() throws Exception {call(CONNECT);}
        public void configure() throws Exception {call(CONFIGURE);}
        public void window() throws Exception {call(WINDOW);}
        public void root() throws Exception {call(ROOT);}
        public void target() throws Exception {call(TARGET);}
        public String attributes() throws Exception {
            call(ATTRIBUTES);samples++;
            if(secondFailure && samples==2) throw new IllegalStateException(PRIVATE);
            return nullAttributes?null:"native-attributes-with-original-timestamp";
        }
        public void release() throws Exception {call(RELEASE);}
        public void cleanup() throws Exception {call(CLEANUP);}
    }
    public static void main(String[] args) {
        String scenario=args[0];Fake f=new Fake();
        ObserverStages.Stage failed=null;ObserverStages.Code expected=null;
        switch(scenario) {
            case "success":break;
            case "environment_unavailable":failed=ENVIRONMENT;expected=INIT_FAILED;f.errors.put(failed,new LinkageError(PRIVATE));break;
            case "automation_null":failed=AUTOMATION;expected=INIT_FAILED;f.errors.put(failed,new ObserverStages.Rejected(expected));break;
            case "constructor_unavailable":failed=AUTOMATION;expected=INIT_FAILED;f.errors.put(failed,new NoSuchMethodException(PRIVATE));break;
            case "connect_failure":failed=CONNECT;expected=CONNECT_FAILED;f.errors.put(failed,new IllegalStateException(PRIVATE));break;
            case "connect_security":failed=CONNECT;expected=PERMISSION_DENIED;f.errors.put(failed,new SecurityException(PRIVATE));break;
            case "wrapped_security":failed=CONNECT;expected=PERMISSION_DENIED;f.errors.put(failed,new InvocationTargetException(new SecurityException(PRIVATE)));break;
            case "configure_null":failed=CONFIGURE;expected=CONFIGURE_FAILED;f.errors.put(failed,new ObserverStages.Rejected(expected));break;
            case "window_missing":failed=WINDOW;expected=WINDOW_UNAVAILABLE;f.errors.put(failed,new ObserverStages.Rejected(expected));break;
            case "window_ambiguous":failed=WINDOW;expected=WINDOW_AMBIGUOUS;f.errors.put(failed,new ObserverStages.Rejected(expected));break;
            case "root_null":failed=ROOT;expected=ROOT_UNAVAILABLE;f.errors.put(failed,new ObserverStages.Rejected(expected));break;
            case "target_missing":failed=TARGET;expected=TARGET_NOT_FOUND;f.errors.put(failed,new ObserverStages.Rejected(expected));break;
            case "target_ambiguous":failed=TARGET;expected=TARGET_AMBIGUOUS;f.errors.put(failed,new ObserverStages.Rejected(expected));break;
            case "target_mismatch":failed=TARGET;expected=TARGET_REJECTED;f.errors.put(failed,new ObserverStages.Rejected(expected));break;
            case "target_security":failed=TARGET;expected=PERMISSION_DENIED;f.errors.put(failed,new SecurityException(PRIVATE));break;
            case "target_exception":failed=TARGET;expected=TARGET_LOOKUP_FAILED;f.errors.put(failed,new IllegalStateException(PRIVATE));break;
            case "visible_unavailable":failed=ATTRIBUTES;expected=ATTRIBUTE_UNAVAILABLE;f.nullAttributes=true;break;
            case "native_getter_unavailable":failed=ATTRIBUTES;expected=ATTRIBUTE_READ_FAILED;f.errors.put(failed,new NoSuchMethodError(PRIVATE));break;
            case "attribute_exception":failed=ATTRIBUTES;expected=ATTRIBUTE_READ_FAILED;f.errors.put(failed,new IllegalStateException(PRIVATE));break;
            case "attribute_security":failed=ATTRIBUTES;expected=PERMISSION_DENIED;f.errors.put(failed,new SecurityException(PRIVATE));break;
            case "second_sample_failure":failed=ATTRIBUTES;expected=ATTRIBUTE_READ_FAILED;f.secondFailure=true;break;
            case "release_failure":failed=RELEASE;expected=DISCONNECT_FAILED;f.errors.put(failed,new IllegalStateException(PRIVATE));break;
            case "cleanup_failure":failed=CLEANUP;expected=DISCONNECT_FAILED;f.errors.put(failed,new IllegalStateException(PRIVATE));break;
            case "body_and_cleanup_failure":failed=ROOT;expected=ROOT_UNAVAILABLE;f.errors.put(failed,new ObserverStages.Rejected(expected));f.errors.put(CLEANUP,new SecurityException(PRIVATE));break;
            case "timeout":failed=CONNECT;expected=OBSERVATION_TIMEOUT;f.errors.put(failed,new TimeoutException(PRIVATE));break;
            case "elapsed_timeout":failed=CONNECT;expected=OBSERVATION_TIMEOUT;f.timeout=true;break;
            case "unknown_exception":failed=ROOT;expected=INTERNAL_UNKNOWN;f.errors.put(failed,new RuntimeException(PRIVATE));break;
            case "interrupted":failed=CONNECT;expected=CONNECT_FAILED;f.errors.put(failed,new InterruptedException(PRIVATE));break;
            case "watchdog_unknown_cleanup": {
                ObserverStages<String> runner=new ObserverStages<>(f,f);f.time+=4_500_000_000L;
                ObserverStages.Outcome<String> out=runner.watchdogResult();
                check(out.code==OBSERVATION_TIMEOUT && out.cleanup.equals("UNKNOWN") && out.frames==null);
                System.out.println(out.failureJson());return;
            }
            case "serialization_failure": {
                ObserverStages<String> runner=new ObserverStages<>(f,f);check(runner.run().code==null);
                ObserverStages.Outcome<String> out=runner.outputFailure(new RuntimeException(PRIVATE));
                check(out.stage==SERIALIZE && out.frames==null && out.status.equals("PARTIAL"));
                System.out.println(out.failureJson());return;
            }
            default:throw new AssertionError("UNKNOWN_OFFLINE_CASE");
        }
        ObserverStages.Outcome<String> out=new ObserverStages<String>(f,f).run();
        check(out.code==expected);check(f.calls.get(CLEANUP)==1);
        check(f.calls.getOrDefault(CONNECT,0)<=1);
        for(Map.Entry<ObserverStages.Stage,Integer> e:f.calls.entrySet()) {
            check(e.getValue()<=(e.getKey()==WINDOW || e.getKey()==ROOT || e.getKey()==TARGET || e.getKey()==ATTRIBUTES || e.getKey()==RELEASE?2:1));
        }
        if(expected==null) {
            check(out.status.equals("SUCCESS") && out.frames.size()==2 && out.cleanup.equals("OK") && out.lastCompleted==CLEANUP);
            System.out.println("SUCCESS_WITH_TWO_NATIVE_FIXTURE_FRAMES");return;
        }
        check(out.stage==failed && out.frames==null);
        if(failed.ordinal()<=TARGET.ordinal()) check(f.samples==0);
        if(failed!=CLEANUP && !f.secondFailure) {
            for(ObserverStages.Stage stage:ObserverStages.Stage.values()) {
                if(stage.ordinal()>failed.ordinal() && stage.ordinal()<CLEANUP.ordinal()) check(!f.calls.containsKey(stage));
            }
        }
        if(failed==CONNECT) check(out.lastCompleted==AUTOMATION);
        if(failed==CLEANUP) check(out.status.equals("PARTIAL") && out.cleanup.equals("FAILED"));
        String wire=out.failureJson();
        check(!wire.contains(PRIVATE) && !wire.contains("native-attributes") && !wire.contains("sample_started") && !wire.contains("sample_finished"));
        check(!wire.contains("hierarchy") && !wire.contains("example.invalid"));
        System.out.println(wire);
    }
}
