import org.pangulu.comsol.PanguModule;
import com.comsol.model.*;
import com.comsol.model.util.ModelUtil;
import java.util.*;
public final class ModuleIntegrationTest {
    private static Model example(String tag,boolean complex,boolean nonlinear,boolean transientCase){
        Model m=ModelUtil.create(tag);
        m.component().create("domain",true);m.component("domain").geom().create("shape",2);
        m.component("domain").geom("shape").create("square","Square");m.component("domain").geom("shape").run();
        m.component("domain").physics().create("eq","CoefficientFormPDE","shape");
        m.component("domain").physics("eq").feature("cfeq1").set("c",nonlinear?"1+u^2":"1");
        if(transientCase)m.component("domain").physics("eq").feature("cfeq1").set("da","1");
        m.component("domain").physics("eq").feature("cfeq1").set("a",complex?"1+i":"1");
        m.component("domain").physics("eq").feature("cfeq1").set("f","1");
        m.component("domain").physics("eq").create("wall","DirichletBoundary",1);
        m.component("domain").physics("eq").feature("wall").selection().all();
        m.component("domain").mesh().create("grid");m.component("domain").mesh("grid").autoMeshSize(4);m.component("domain").mesh("grid").run();
        m.study().create("experiment");m.study("experiment").create("step",transientCase?"Transient":"Stationary");if(transientCase)m.study("experiment").feature("step").set("tlist","range(0,0.05,0.1)");m.study("experiment").createAutoSequences("all");
        return m;
    }
    public static void main(String[]args)throws Exception{
        ModelUtil.initStandalone(false);ModelUtil.showProgress(true);
        for(int mode=0;mode<4;mode++){
            boolean complex=mode==1;String name=new String[]{"real","complex","nonlinear","transient"}[mode];
            Model m=example(name+"Case",complex,mode==2,mode==3);
            String[] targets=PanguModule.targets(m);String target=Arrays.stream(targets).filter(t->t.endsWith("fc1")).findFirst().orElse(targets[0]);
            String solver=target.split("/")[0];SolverSequence sol=m.sol(solver);
            sol.runAll();int number=Math.max(1,sol.getPVals().length);double[] real=sol.getU(number),imag=sol.getUImag(number);m.save(args[0]+"/"+name+"_cpu.mph");
            String before=PanguModule.inspect(m);System.out.println("MODEL_BEFORE\n"+before);
            PanguModule.apply(m,target);sol.runAll();double[] r=sol.getU(number),i=sol.getUImag(number);
            if(r.length!=real.length||i.length!=imag.length)throw new IllegalStateException("DOF count changed");
            double error=0,norm=0;for(int k=0;k<r.length;k++){double di=(i.length==0?0:i[k]-imag[k]);error+=(r[k]-real[k])*(r[k]-real[k])+di*di;norm+=real[k]*real[k]+(imag.length==0?0:imag[k]*imag[k]);}
            double relative=Math.sqrt(error/norm);if(!Double.isFinite(relative)||relative>1e-8)throw new IllegalStateException("Relative error "+relative);
            m.save(args[0]+"/"+name+"_pangu.mph");
            System.out.println("MODEL_PASS case="+name+" complex="+complex+" dof="+r.length+" relative_L2="+relative+" target="+target);
            PanguModule.restore(m,target);String restored=PanguModule.inspect(m);if(!Arrays.stream(restored.split("\n")).filter(t->t.startsWith(target+"\t")).findFirst().get().equals(Arrays.stream(before.split("\n")).filter(t->t.startsWith(target+"\t")).findFirst().get()))throw new IllegalStateException("Restore failed");
            ModelUtil.remove(m.tag());
        }
        System.out.println("MODULE_INTEGRATION_PASS");
    }
}
