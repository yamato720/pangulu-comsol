import com.comsol.model.*;
import com.comsol.model.util.ModelUtil;
import org.pangulu.comsol.PanguModule;
import java.nio.ByteBuffer;
import java.security.MessageDigest;
import java.util.*;

/** Registration must preserve solver references, activation and saved data. */
public final class GlobalOptionsTest {
    private static SolverFeature feature(Model m,String path){
        String[] p=path.split("/");SolverFeature f=m.sol(p[0]).feature(p[1]);
        for(int i=2;i<p.length;i++)f=f.feature(p[i]);return f;
    }
    private static Map<String,String> selections(Model m){
        Map<String,String> out=new TreeMap<>();
        for(String path:PanguModule.targets(m))out.put(path,feature(m,path).getString("linsolver"));return out;
    }
    private static void active(SolverFeature f,String path,Map<String,Boolean> out){
        out.put(path,f.isActive());for(String t:f.feature().tags())active(f.feature(t),path+"/"+t,out);
    }
    private static Map<String,Boolean> activation(Model m){
        Map<String,Boolean> out=new TreeMap<>();for(String s:m.sol().tags())for(String f:m.sol(s).feature().tags())active(m.sol(s).feature(f),s+"/"+f,out);return out;
    }
    private static void doubles(MessageDigest md,double[] values){
        ByteBuffer b=ByteBuffer.allocate(8192);
        for(double v:values){if(!Double.isFinite(v))throw new IllegalStateException("Nonfinite input");if(b.remaining()<8){md.update(b.array(),0,b.position());b.clear();}b.putLong(Double.doubleToRawLongBits(v));}
        md.update(b.array(),0,b.position());
    }
    private static String solutions(Model m)throws Exception{
        MessageDigest md=MessageDigest.getInstance("SHA-256");
        for(String tag:m.sol().tags()){
            SolverSequence s=m.sol(tag);double[] pv=s.getPVals();doubles(md,pv);
            doubles(md,s.getU());doubles(md,s.getUImag());
            for(int i=1;i<=Math.max(1,pv.length);i++){doubles(md,s.getU(i));doubles(md,s.getUImag(i));}
        }
        StringBuilder b=new StringBuilder();for(byte v:md.digest())b.append(String.format("%02x",v&255));return b.toString();
    }
    private static void check(Model m,String out,String label,int expectedOwners)throws Exception{
        Map<String,String> before=selections(m);Map<String,Boolean> states=activation(m);String data=solutions(m);
        String registered=PanguModule.registerAll(m);
        if(registered.split("\n").length!=expectedOwners)throw new IllegalStateException("Unexpected owner count: "+registered);
        if(!before.equals(selections(m)))throw new IllegalStateException("Automatic selection changed");
        for(Map.Entry<String,Boolean> e:states.entrySet())if(feature(m,e.getKey()).isActive()!=e.getValue())throw new IllegalStateException("Activation changed: "+e.getKey());
        if(!data.equals(solutions(m)))throw new IllegalStateException("Saved solution data changed");
        String tree=PanguModule.inspect(m);PanguModule.registerAll(m);
        if(!tree.equals(PanguModule.inspect(m)))throw new IllegalStateException("Registration is not idempotent");
        m.save(out);
        System.out.println("GLOBAL_OPTIONS_REGISTER_PASS case="+label+" owners="+expectedOwners+" selections="+before+" solutions_sha256="+data+" output="+out);
        System.out.println("REGISTERED_OWNERS_BEGIN\n"+registered+"\nREGISTERED_OWNERS_END");System.out.flush();
    }
    public static void main(String[]args)throws Exception{
        try{
        ModelUtil.initStandalone(false);
        Model small=ModelUtil.load("small",args[0]);check(small,args[2]+"/small-options.mph","small",1);
        String target=PanguModule.targets(small)[0],old=feature(small,target).getString("linsolver");
        PanguModule.apply(small,target);PanguModule.restore(small,target);
        if(!old.equals(feature(small,target).getString("linsolver")))throw new IllegalStateException("Explicit apply/restore failed");
        ModelUtil.remove("small");
        Model full=ModelUtil.load("full",args[1]);check(full,args[2]+"/full-options.mph","full_S",5);ModelUtil.remove("full");
        System.out.println("GLOBAL_OPTIONS_TEST_COMPLETE");System.out.flush();
        }catch(Exception|Error e){e.printStackTrace(System.err);throw e;}
    }
}
