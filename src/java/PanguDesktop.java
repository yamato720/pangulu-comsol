package org.pangulu.comsol;

import com.comsol.model.Model;
import com.comsol.model.util.ModelUtil;
import com.comsol.gui.CsApplication;
import com.comsol.gui.DesktopWindow;
import com.comsol.guimph.MphDesktopWindow;
import org.eclipse.swt.widgets.Display;
import org.osgi.framework.BundleActivator;
import org.osgi.framework.BundleContext;
import java.net.URI;
import java.nio.file.*;
import java.util.*;

/** Session-scoped desktop extension; never runs or saves a model. */
public final class PanguDesktop implements BundleActivator {
    private volatile boolean stopped;
    private Path session;
    private String lastError="";
    private final Map<String,String> signatures=new HashMap<>();
    private final Map<String,String> reports=new LinkedHashMap<>();
    private static String quote(String s){if(s==null)s="";return "\""+s.replace("\\","\\\\").replace("\"","\\\"").replace("\n","\\n").replace("\r","\\r").replace("\t","\\t")+"\"";}
    private void log(String message){
        try{Files.write(session.resolve("pangu-options.log"),(java.time.Instant.now()+" "+message+"\n").getBytes(java.nio.charset.StandardCharsets.UTF_8),StandardOpenOption.CREATE,StandardOpenOption.APPEND);}catch(Exception e){System.err.println("PANGU_OPTIONS "+e);}
    }
    private void publish(){
        String data="{\"enabled\":true,\"automatic_activation\":false,\"automatic_save\":false,\"clears_solutions\":false,\"updated_at\":"+quote(java.time.Instant.now().toString())+",\"last_error\":"+quote(lastError)+",\"models\":["+String.join(",",reports.values())+"]}\n";
        try{Path temp=session.resolve("pangu-options.json.tmp");Files.write(temp,data.getBytes(java.nio.charset.StandardCharsets.UTF_8));Files.move(temp,session.resolve("pangu-options.json"),StandardCopyOption.REPLACE_EXISTING);}catch(Exception e){log("STATUS_ERROR "+e);}
    }
    private boolean busy(){
        CsApplication app=CsApplication.getInstance();
        if(app==null||app.getActiveDesktop()==null)return true;
        Iterator<DesktopWindow> windows=app.getWindowManager().iterator();
        while(windows.hasNext()){
            DesktopWindow window=windows.next();
            if(window.isBlocked()||window.isAboutToClose())return true;
            if(window instanceof MphDesktopWindow){
                com.comsol.model.dbimodel.Model model=((MphDesktopWindow)window).getModel();
                if(model!=null&&model.isRunning())return true;
            }
        }
        return false;
    }
    private void refreshTrees(){
        CsApplication app=CsApplication.getInstance();if(app==null)return;
        Iterator<DesktopWindow> windows=app.getWindowManager().iterator();
        while(windows.hasNext()){
            DesktopWindow window=windows.next();
            if(window instanceof MphDesktopWindow&&!window.isClosed())((MphDesktopWindow)window).updateModelTreeView(false);
        }
    }
    private void tick(Display display){
        if(stopped||display.isDisposed())return;
        try{
            if(!busy()){
                String version=ModelUtil.getComsolVersion();
                if(!version.contains("6.4"))throw new IllegalStateException("Desktop integration is validated for COMSOL 6.4; found "+version);
                Set<String> live=new HashSet<>(Arrays.asList(ModelUtil.tags()));
                boolean changed=reports.keySet().retainAll(live);signatures.keySet().retainAll(live);
                for(String tag:live){
                    Model model=ModelUtil.model(tag);if(model.isReadOnly())continue;
                    String source=model.getFilePath();
                    String signature=source+"\n"+PanguModule.inspect(model);
                    if(signature.equals(signatures.get(tag)))continue;
                    String owners=PanguModule.registerAll(model);
                    signatures.put(tag,source+"\n"+PanguModule.inspect(model));
                    int count=owners.isEmpty()?0:owners.split("\n").length;
                    reports.put(tag,"{\"model_tag\":"+quote(tag)+",\"file\":"+quote(source)+",\"owner_count\":"+count+",\"owners\":"+quote(owners)+",\"selections_preserved\":true}");
                    log("REGISTERED model="+tag+" owners="+count+" selections_preserved=true file="+source);changed=true;
                }
                if(changed){lastError="";refreshTrees();publish();}
            }
        }catch(Throwable e){
            String text=e.toString();if(!text.equals(lastError)){lastError=text;log("DEFERRED "+text);publish();}
        }finally{if(!stopped&&!display.isDisposed())display.timerExec(2000,()->tick(display));}
    }
    @Override public void start(BundleContext context){
        String location=System.getProperty("pangu.session.uri");
        if(location==null||location.isEmpty()){stopped=true;return;}
        session=Paths.get(URI.create(location));
        log("STARTING session-scoped global choices; no automatic selection, clearing or saving");publish();
        Thread finder=new Thread(()->{
            while(!stopped){
                try{
                    for(Thread thread:Thread.getAllStackTraces().keySet()){
                        Display display=Display.findDisplay(thread);
                        if(display!=null&&!display.isDisposed()){display.asyncExec(()->tick(display));return;}
                    }
                    Thread.sleep(500);
                }catch(InterruptedException e){return;}catch(Throwable e){log("WAITING "+e);try{Thread.sleep(1000);}catch(InterruptedException stop){return;}}
            }
        },"Pangu-desktop-startup");finder.setDaemon(true);finder.start();
    }
    @Override public void stop(BundleContext context){stopped=true;}
}
