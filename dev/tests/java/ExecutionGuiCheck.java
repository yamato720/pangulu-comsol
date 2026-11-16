package org.pangulu.verify;
import com.comsol.model.*;
import com.comsol.model.util.ModelUtil;
import com.comsol.gui.CsApplication;
import com.comsol.guimph.MphDesktopWindow;
import org.eclipse.swt.widgets.Display;
import org.osgi.framework.*;
import java.nio.file.*;
import java.net.URI;

/** Test bundle only: select the registered GUI option and compare a CPU fixture. */
public class ExecutionGuiCheck implements BundleActivator {
 private volatile boolean stop;private Path output;
 private void tick(Display display){
  if(stop||display.isDisposed())return;
  try{
   if(CsApplication.getInstance()==null||CsApplication.getInstance().getActiveDesktop()==null){display.timerExec(500,()->tick(display));return;}
   for(String tag:ModelUtil.tags()){
    Model model=ModelUtil.model(tag);
    if(model.getFilePath()==null||!model.getFilePath().endsWith("-work.mph")||model.sol().tags().length==0)continue;
    SolverFeature own=model.sol("sol1").feature("s1");String pangu=null;
    for(String t:own.feature().tags()){
     SolverFeature feature=own.feature(t);String note=feature.comments();
     if("Direct".equals(feature.getType())&&note!=null&&note.startsWith("PANGU_COMSOL"))pangu=t;
    }
    if(pangu==null){display.timerExec(500,()->tick(display));return;}
    double[] reference=model.sol("sol1").getU();String old=own.feature("fc1").getString("linsolver");
    own.feature("fc1").set("linsolver",pangu);model.sol("sol1").runAll();double[] result=model.sol("sol1").getU();
    if(result.length!=reference.length)throw new IllegalStateException("DOF count changed");
    double error=0,norm=0;
    for(int i=0;i<result.length;i++){if(!Double.isFinite(result[i]))throw new IllegalStateException("Nonfinite");error+=(result[i]-reference[i])*(result[i]-reference[i]);norm+=reference[i]*reference[i];}
    double relative=Math.sqrt(error/norm);if(!Double.isFinite(relative)||relative>1e-8)throw new IllegalStateException("CPU reference mismatch "+relative);
    own.feature("fc1").set("linsolver",old);model.save(model.getFilePath());
    Files.writeString(output,"{\"status\":\"completed\",\"dof\":"+result.length+",\"relative_L2\":"+relative+"}");
    stop=true;display.timerExec(1500,()->((MphDesktopWindow)CsApplication.getInstance().getActiveDesktop()).handleExitAction(false));return;
   }
  }catch(Throwable error){
   try{Files.writeString(output,"{\"status\":\"failed\",\"error\":\""+error.toString().replace("\\","\\\\").replace("\"","\\\"")+"\"}");}catch(Exception ignored){}
   stop=true;return;
  }
  if(!stop)display.timerExec(500,()->tick(display));
 }
 public void start(BundleContext context){
  output=Paths.get(URI.create(System.getProperty("verify.execution.output")));
  Thread finder=new Thread(()->{while(!stop){
   try{for(Thread thread:Thread.getAllStackTraces().keySet()){Display display=Display.findDisplay(thread);if(display!=null&&!display.isDisposed()){display.asyncExec(()->tick(display));return;}}Thread.sleep(300);}catch(Exception error){return;}
  }});finder.setDaemon(true);finder.start();
 }
 public void stop(BundleContext context){stop=true;}
}
