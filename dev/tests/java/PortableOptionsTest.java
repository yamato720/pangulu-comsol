import com.comsol.model.*;
import com.comsol.model.util.ModelUtil;
import java.nio.ByteBuffer;
import java.security.MessageDigest;

/** Deliberately has no PanguLU Java dependency: ordinary COMSOL must read it. */
public final class PortableOptionsTest {
    private static void add(MessageDigest md,double[] values){ByteBuffer b=ByteBuffer.allocate(8192);for(double v:values){if(b.remaining()<8){md.update(b.array(),0,b.position());b.clear();}b.putLong(Double.doubleToRawLongBits(v));}md.update(b.array(),0,b.position());}
    private static String solutions(Model m)throws Exception{
        MessageDigest md=MessageDigest.getInstance("SHA-256");
        for(String tag:m.sol().tags()){
            SolverSequence s=m.sol(tag);double[] pv=s.getPVals();add(md,pv);add(md,s.getU());add(md,s.getUImag());
            for(int i=1;i<=Math.max(1,pv.length);i++){add(md,s.getU(i));add(md,s.getUImag(i));}
        }
        StringBuilder b=new StringBuilder();for(byte v:md.digest())b.append(String.format("%02x",v&255));return b.toString();
    }
    public static void main(String[]args)throws Exception{
        try{
            ModelUtil.initStandalone(false);
            Model m=ModelUtil.load("ordinarySmall",args[0]);SolverSequence s=m.sol("sol1");
            String selected=s.feature("s1").feature("fc1").getString("linsolver");
            if(selected.startsWith("pangu"))throw new IllegalStateException("CPU selection was replaced");
            double[] before=s.getU();s.runAll();double[] after=s.getU();double err=0,norm=0;
            for(int i=0;i<before.length;i++){err+=(after[i]-before[i])*(after[i]-before[i]);norm+=before[i]*before[i];}
            double relative=Math.sqrt(err/norm);if(!Double.isFinite(relative)||relative>1e-10)throw new IllegalStateException("CPU recompute mismatch "+relative);
            System.out.println("ORDINARY_CPU_RECOMPUTE_PASS relative_L2="+relative+" selected="+selected);ModelUtil.remove("ordinarySmall");
            m=ModelUtil.load("ordinaryFull",args[1]);
            if(m.sol("sol6").getPVals().length!=59)throw new IllegalStateException("Frequency count differs");
            if(!solutions(m).equals(args[2]))throw new IllegalStateException("Portable full-model data changed");
            System.out.println("ORDINARY_FULL_MODEL_OPEN_PASS frequencies=59 all_saved_vectors_unchanged=true");ModelUtil.remove("ordinaryFull");
            System.out.println("PORTABLE_OPTIONS_TEST_COMPLETE");System.out.flush();
        }catch(Exception|Error e){e.printStackTrace(System.err);throw e;}
    }
}
