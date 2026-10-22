package org.digitallife;

import java.lang.reflect.InvocationTargetException;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeoutException;

/** The same small lifecycle is exercised on the host with an injected backend.
 * No Android stubs, permission simulation, transport, input, logging or retries.
 */
public final class ObserverStages<T> {
    public enum Stage { PROCESS, ENVIRONMENT, AUTOMATION, CONNECT, CONFIGURE,
        WINDOW, ROOT, TARGET, ATTRIBUTES, RELEASE, CLEANUP, SERIALIZE }
    public enum Code { INIT_FAILED, CONNECT_FAILED, CONFIGURE_FAILED,
        WINDOW_UNAVAILABLE, WINDOW_AMBIGUOUS, ROOT_UNAVAILABLE, TARGET_NOT_FOUND,
        TARGET_AMBIGUOUS, TARGET_REJECTED, TARGET_LOOKUP_FAILED,
        ATTRIBUTE_READ_FAILED, ATTRIBUTE_UNAVAILABLE, DISCONNECT_FAILED,
        OBSERVATION_TIMEOUT, PERMISSION_DENIED, INTERNAL_UNKNOWN, INVALID_NONCE }
    public static final class Rejected extends Exception {
        public final Code code;
        public Rejected(Code code) { super(); this.code = code; }
    }
    public interface Backend<T> {
        void environment() throws Exception;
        void automation() throws Exception;
        void connect() throws Exception;
        void configure() throws Exception;
        void window() throws Exception;
        void root() throws Exception;
        void target() throws Exception;
        T attributes() throws Exception;
        void release() throws Exception;
        void cleanup() throws Exception;
    }
    public interface Clock { long now(); }
    public static final class Outcome<T> {
        public final String status, exceptionType, cleanup, cleanupException;
        public final Code code;
        public final Stage stage, lastCompleted;
        public final long durationNs;
        // Never expose partial frames, or failed observation timestamps, on wire.
        public final List<T> frames;
        Outcome(String status, Code code, Stage stage, Stage completed, String type,
                long duration, String cleanup, String cleanupException, List<T> frames) {
            this.status=status; this.code=code; this.stage=stage; lastCompleted=completed;
            exceptionType=type; durationNs=duration; this.cleanup=cleanup;
            this.cleanupException=cleanupException; this.frames=frames;
        }
        public String failureJson() {
            if (code == null) throw new IllegalStateException();
            return "{\"schema\":\"find-x6-observer-stage/2\",\"status\":\""+status
                +"\",\"code\":\""+code+"\",\"stage\":\""+stage
                +"\",\"last_completed_stage\":\""+lastCompleted
                +"\",\"exception_type\":\""+exceptionType+"\",\"duration_ns\":"+durationNs
                +",\"cleanup\":{\"status\":\""+cleanup+"\",\"exception_type\":\""+cleanupException+"\"}}";
        }
    }
    private final Backend<T> backend;
    private final Clock clock;
    private final long started;
    private volatile Stage stage=Stage.PROCESS, completed=Stage.PROCESS;
    private volatile String cleanup="UNKNOWN";
    public ObserverStages(Backend<T> backend, Clock clock) {
        this.backend=backend; this.clock=clock; started=clock.now();
    }
    private long duration() { return Math.max(0,clock.now()-started); }
    private void enter(Stage next) throws TimeoutException {
        stage=next;
        if (duration()>=4_500_000_000L) throw new TimeoutException();
    }
    private void done() throws TimeoutException {
        if (duration()>=4_500_000_000L) throw new TimeoutException();
        completed=stage;
    }
    private static Throwable unwrap(Throwable e) {
        for (int i=0;i<8 && e instanceof InvocationTargetException && e.getCause()!=null;i++) e=e.getCause();
        return e;
    }
    static String type(Throwable e) {
        e=unwrap(e);
        if (e instanceof Rejected) return "REJECTED";
        if (e instanceof SecurityException) return "SECURITY_EXCEPTION";
        if (e instanceof TimeoutException) return "TIMEOUT_EXCEPTION";
        if (e instanceof InterruptedException) return "INTERRUPTED_EXCEPTION";
        if (e instanceof IllegalStateException) return "ILLEGAL_STATE_EXCEPTION";
        if (e instanceof ReflectiveOperationException) return "REFLECTION_EXCEPTION";
        if (e instanceof LinkageError) return "LINKAGE_ERROR";
        return "OTHER";
    }
    private static Code classify(Throwable error, Stage at) {
        Throwable e=unwrap(error);
        if (e instanceof Rejected) return ((Rejected)e).code;
        if (e instanceof SecurityException) return Code.PERMISSION_DENIED;
        if (e instanceof TimeoutException) return Code.OBSERVATION_TIMEOUT;
        if (type(e).equals("OTHER")) return Code.INTERNAL_UNKNOWN;
        switch (at) {
            case PROCESS: case ENVIRONMENT: case AUTOMATION: return Code.INIT_FAILED;
            case CONNECT: return Code.CONNECT_FAILED;
            case CONFIGURE: return Code.CONFIGURE_FAILED;
            case WINDOW: return Code.WINDOW_UNAVAILABLE;
            case ROOT: return Code.ROOT_UNAVAILABLE;
            case TARGET: return Code.TARGET_LOOKUP_FAILED;
            case ATTRIBUTES: return Code.ATTRIBUTE_READ_FAILED;
            case RELEASE: case CLEANUP: return Code.DISCONNECT_FAILED;
            default: return Code.INTERNAL_UNKNOWN;
        }
    }
    public Outcome<T> watchdogResult() {
        return new Outcome<>("FAILED",Code.OBSERVATION_TIMEOUT,stage,completed,"TIMEOUT_EXCEPTION",
            duration(),cleanup,"NONE",null);
    }
    public Outcome<T> outputFailure(Throwable error) {
        return new Outcome<>("PARTIAL",Code.INTERNAL_UNKNOWN,Stage.SERIALIZE,completed,type(error),
            duration(),cleanup,"NONE",null);
    }
    public Outcome<T> run() {
        Code code=null; String type="NONE", cleanupType="NONE";
        Stage failed=null, bodyCompleted; boolean bodySuccess=false;
        List<T> frames=new ArrayList<>();
        try {
            enter(Stage.ENVIRONMENT); backend.environment(); done();
            enter(Stage.AUTOMATION); backend.automation(); done();
            enter(Stage.CONNECT); backend.connect(); done();
            enter(Stage.CONFIGURE); backend.configure(); done();
            for (int i=0;i<2;i++) {
                enter(Stage.WINDOW); backend.window(); done();
                enter(Stage.ROOT); backend.root(); done();
                enter(Stage.TARGET); backend.target(); done();
                enter(Stage.ATTRIBUTES); T frame=backend.attributes();
                if (frame==null) throw new Rejected(Code.ATTRIBUTE_UNAVAILABLE);
                done(); frames.add(frame);
                enter(Stage.RELEASE); backend.release(); done();
            }
            bodySuccess=true;
        } catch (Throwable error) {
            failed=stage; code=classify(error,stage); type=type(error);
        } finally {
            bodyCompleted=completed;
            stage=Stage.CLEANUP;
            try { backend.cleanup(); cleanup="OK"; completed=Stage.CLEANUP; }
            catch (Throwable error) {
                cleanup="FAILED"; cleanupType=type(error);
                if (code==null) {code=Code.DISCONNECT_FAILED;failed=Stage.CLEANUP;type=cleanupType;}
            }
        }
        return new Outcome<>(code==null?"SUCCESS":bodySuccess?"PARTIAL":"FAILED",code,
            failed==null?Stage.CLEANUP:failed,code==null?completed:bodyCompleted,type,duration(),
            cleanup,cleanupType,code==null?frames:null);
    }
}
