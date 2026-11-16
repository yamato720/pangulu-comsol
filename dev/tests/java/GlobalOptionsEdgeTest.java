import com.comsol.model.*;import com.comsol.model.util.ModelUtil;import org.pangulu.comsol.PanguModule;import java.util.*;
public class GlobalOptionsEdgeTest {
 public static void main(String[]args)throws Exception{try{
 ModelUtil.initStandalone(false);Model m=ModelUtil.load("time",args[0]);SolverSequence sol=m.sol("sol1");double[] times=sol.getPVals();double[][] before=new double[times.length][];for(int i=0;i<times.length;i++)before[i]=sol.getU(i+1);
 String target=Arrays.stream(PanguModule.targets(m)).filter(p->p.endsWith("fc1")).findFirst().get();String[] p=target.split("/");SolverFeature own=sol.feature(p[1]),consumer=own.feature(p[2]);String old=consumer.getString("linsolver");
 PanguModule.registerAll(m);if(!old.equals(consumer.getString("linsolver")))throw new Exception("Time selection changed");for(int i=0;i<times.length;i++)if(!Arrays.equals(before[i],sol.getU(i+1)))throw new Exception("Time data changed");
 own.feature("pangu").set("linsolver","mumps");PanguModule.registerAll(m);if(!"mumps".equals(own.feature("pangu").getString("linsolver"))||!"cudss".equals(own.feature("pangu1").getString("linsolver")))throw new Exception("Repurposed node was modified or reused");
 consumer.set("linsolver","pangu");PanguModule.apply(m,target);if(!"pangu1".equals(consumer.getString("linsolver")))throw new Exception("Not using GPU option");PanguModule.restore(m,target);if(!"pangu".equals(consumer.getString("linsolver")))throw new Exception("Did not restore the previous choice");
 System.out.println("GLOBAL_TIME_AND_REPURPOSED_NODE_PASS time_points="+times.length);System.out.println("GLOBAL_EDGE_TEST_COMPLETE");ModelUtil.remove("time");
 }catch(Exception|Error e){e.printStackTrace();throw e;}}
}