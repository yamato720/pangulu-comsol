package org.pangulu.comsol;
import com.comsol.model.*;
import com.comsol.model.util.ModelUtil;
import java.util.*;
import java.nio.file.*;

/** Model-independent API for the COMSOL Java Shell, methods, and batch jobs. */
public final class PanguModule {
    private static final String MARKER="PANGU_COMSOL_MODULE_V1\n";
    private static final String OPTION_MARKER="PANGU_COMSOL_OPTION_V1\n";
    private static boolean has(SolverFeature f,String name){return Arrays.asList(f.properties()).contains(name);}
    private static boolean eligible(SolverFeature f){
        return Arrays.asList("FullyCoupled","SegregatedStep","Eigenvalue","Time").contains(f.getType())&&has(f,"linsolver");
    }
    private static void scan(SolverFeature f,String path,List<String> lines,List<String> targets){
        String active="";if(has(f,"linsolver"))try{active=f.getString("linsolver");}catch(Exception ignored){}
        lines.add(path+"\t"+f.getType()+"\t"+f.label()+"\t"+active+(eligible(f)?"\tSELECTABLE":""));
        if(eligible(f))targets.add(path);
        for(String t:f.feature().tags())scan(f.feature(t),path+"/"+t,lines,targets);
    }
    public static String inspect(Model model){
        List<String> lines=new ArrayList<>(),targets=new ArrayList<>();
        for(String s:model.sol().tags())for(String t:model.sol(s).feature().tags())scan(model.sol(s).feature(t),s+"/"+t,lines,targets);
        return String.join("\n",lines);
    }
    public static String[] targets(Model model){
        List<String> lines=new ArrayList<>(),targets=new ArrayList<>();
        for(String s:model.sol().tags())for(String t:model.sol(s).feature().tags())scan(model.sol(s).feature(t),s+"/"+t,lines,targets);
        return targets.toArray(new String[0]);
    }
    private static SolverFeature feature(Model model,String[] parts,int last){
        SolverFeature f=model.sol(parts[0]).feature(parts[1]);
        for(int i=2;i<=last;i++)f=f.feature(parts[i]);return f;
    }
    private static SolverFeature owner(Model model,String[] p){
        for(int i=p.length-1;i>=1;i--){SolverFeature f=feature(model,p,i);if(Arrays.asList("Stationary","Time","Eigenvalue").contains(f.getType()))return f;}
        throw new IllegalArgumentException("No Stationary/Time/Eigenvalue owner: "+String.join("/",p));
    }
    private static String ownerPath(Model model,String[] p){
        for(int i=p.length-1;i>=1;i--)if(Arrays.asList("Stationary","Time","Eigenvalue").contains(feature(model,p,i).getType()))return String.join("/",Arrays.copyOf(p,i+1));
        throw new IllegalArgumentException("No supported owner");
    }
    private static final class Binding {
        final SolverFeature feature;final String path,value;final boolean active;
        Binding(SolverFeature f,String p){feature=f;path=p;value=has(f,"linsolver")?f.getString("linsolver"):null;active=f.isActive();}
    }
    private static void snapshot(SolverFeature f,String path,List<Binding> bindings){
        bindings.add(new Binding(f,path));
        for(String tag:f.feature().tags())snapshot(f.feature(tag),path+"/"+tag,bindings);
    }
    private static void restoreBindings(List<Binding> bindings){
        for(Binding b:bindings)if(b.feature.isActive()!=b.active)b.feature.active(b.active);
        // A newly created Direct can change the selected linear solver. Restore
        // references last, after activation callbacks have finished.
        for(Binding b:bindings)if(b.value!=null&&!b.value.equals(b.feature.getString("linsolver")))b.feature.set("linsolver",b.value);
        for(Binding b:bindings)if(b.value!=null&&!b.value.equals(b.feature.getString("linsolver")))throw new IllegalStateException("Linear solver selection changed: "+b.path);
    }
    private static void labelOption(SolverFeature own,SolverFeature direct){
        Set<String> used=new HashSet<>();
        for(String t:own.feature().tags())if(!t.equals(direct.tag()))used.add(own.feature(t).label());
        String label="PanguLU";int n=2;while(used.contains(label))label="PanguLU "+(n++);
        if(!label.equals(direct.label()))direct.label(label);
    }
    private static void configureDirect(SolverFeature own,SolverFeature direct){
        labelOption(own,direct);direct.set("linsolver","cudss");direct.set("cudssprecision","double");
        String hybrid=has(direct,"cudsshybridmemory")?"cudsshybridmemory":"cudssusehybridmemory";
        direct.set(hybrid,"never");direct.set("cudssusehybridcompute","off");direct.set("cudssreusereorder","on");
    }
    /** Add a standard, initially unselected Direct option without clearing data. */
    private static String ensureOption(Model model,String target){
        String[] p=target.split("/");SolverFeature own=owner(model,p);String path=ownerPath(model,p);
        for(String t:own.feature().tags()){
            SolverFeature f=own.feature(t);String note=f.comments();
            if("Direct".equals(f.getType())&&"cudss".equals(f.getString("linsolver"))&&note!=null&&(note.startsWith(OPTION_MARKER+path+"\n")||note.startsWith(MARKER+path+"/"))){
                labelOption(own,f);return t;
            }
        }
        List<Binding> bindings=new ArrayList<>();snapshot(own,path,bindings);
        for(Binding b:bindings)if(eligible(b.feature)&&(b.value==null||b.value.isEmpty()))throw new IllegalStateException("Solver sequence is still being created: "+b.path);
        String tag="pangu";int n=1;while(Arrays.asList(own.feature().tags()).contains(tag))tag="pangu"+(n++);
        SolverFeature direct=null;
        try{
            direct=own.create(tag,"Direct");configureDirect(own,direct);
            StringBuilder note=new StringBuilder(OPTION_MARKER).append(path).append('\n');
            for(Binding b:bindings)if(eligible(b.feature)&&b.value!=null)note.append(b.path).append('\t').append(b.value).append('\n');
            direct.comments(note.toString());
        }catch(RuntimeException|Error e){
            try{restoreBindings(bindings);}catch(RuntimeException rollback){e.addSuppressed(rollback);}
            try{if(direct!=null)own.feature().remove(tag);}catch(RuntimeException rollback){e.addSuppressed(rollback);}
            throw e;
        }finally{restoreBindings(bindings);}
        direct.active(false);
        return tag;
    }
    /** Register choices across the model; preserve selections and stored solutions. */
    public static String registerAll(Model model){
        Set<String> owners=new LinkedHashSet<>();List<String> lines=new ArrayList<>();
        for(String target:targets(model)){
            String path=ownerPath(model,target.split("/"));if(!owners.add(path))continue;
            String tag=ensureOption(model,target);lines.add(path+"\t"+tag);
        }
        return String.join("\n",lines);
    }
    public static String apply(Model model,String target){
        if(target==null||target.isEmpty()||target.equals("auto")){
            String[] candidates=targets(model);if(candidates.length!=1)throw new IllegalArgumentException("Choose one target from: "+Arrays.toString(candidates));target=candidates[0];
        }
        String[] p=target.split("/");if(p.length<2)throw new IllegalArgumentException("Use solTag/operationTag/consumerTag");
        SolverFeature consumer=feature(model,p,p.length-1),owner=owner(model,p);
        if(!eligible(consumer))throw new IllegalArgumentException("Unsupported consumer: "+consumer.getType());
        String old=consumer.getString("linsolver");
        for(String t:owner.feature().tags()){
            SolverFeature f=owner.feature(t);String note=f.comments();
            if("Direct".equals(f.getType())&&note!=null&&note.startsWith(MARKER+target+"\n")&&"cudss".equals(f.getString("linsolver"))){labelOption(owner,f);consumer.set("linsolver",t);model.sol(p[0]).clearSolution();return t;}
        }
        String path=ownerPath(model,p);
        for(String t:owner.feature().tags()){
            SolverFeature f=owner.feature(t);String note=f.comments();
            if(note!=null&&note.startsWith(OPTION_MARKER+path+"\n")&&"cudss".equals(f.getString("linsolver"))){
                labelOption(owner,f);
                StringBuilder updated=new StringBuilder(OPTION_MARKER).append(path).append('\n');
                for(String line:note.substring((OPTION_MARKER+path+"\n").length()).split("\n"))if(!line.startsWith(target+"\t")&&!line.isEmpty())updated.append(line).append('\n');
                String previous=old;
                if(old.equals(t))for(String line:note.split("\n"))if(line.startsWith(target+"\t"))previous=line.substring(target.length()+1);
                updated.append(target).append('\t').append(previous).append('\n');f.comments(updated.toString());
                consumer.set("linsolver",t);model.sol(p[0]).clearSolution();return t;
            }
        }
        String tag="pangu";int n=1;while(Arrays.asList(owner.feature().tags()).contains(tag))tag="pangu"+(n++);
        SolverFeature direct=owner.create(tag,"Direct");configureDirect(owner,direct);
        direct.comments(MARKER+target+"\n"+old);consumer.set("linsolver",tag);model.sol(p[0]).clearSolution();return tag;
    }
    public static void restore(Model model,String target){
        String[] p=target.split("/");SolverFeature consumer=feature(model,p,p.length-1),owner=owner(model,p);
        List<String> tags=new ArrayList<>(Arrays.asList(owner.feature().tags()));String selected=consumer.getString("linsolver");
        if(tags.remove(selected))tags.add(0,selected);
        for(String t:tags){
            String note=owner.feature(t).comments();
            if(note!=null&&note.startsWith(MARKER+target+"\n")){consumer.set("linsolver",note.split("\n",-1)[2]);model.sol(p[0]).clearSolution();return;}
            if(note!=null&&note.startsWith(OPTION_MARKER))for(String line:note.split("\n"))if(line.startsWith(target+"\t")){consumer.set("linsolver",line.substring(target.length()+1));model.sol(p[0]).clearSolution();return;}
        }
        throw new IllegalArgumentException("No saved PanguLU configuration for "+target);
    }
    public static void main(String[]args)throws Exception{
        if(args.length!=5)throw new IllegalArgumentException("action input.mph output.mph target-or-auto run-flag");
        ModelUtil.initStandalone(false);ModelUtil.showProgress(true);Model model=ModelUtil.load("panguModel",args[1]);
        System.out.println("PANGU_MODEL_INSPECT_BEGIN\n"+inspect(model)+"\nPANGU_MODEL_INSPECT_END");
        if(args[0].equals("inspect")){ModelUtil.remove("panguModel");return;}
        String target=args[3];if(target.equals("auto")){String[]t=targets(model);if(t.length!=1)throw new IllegalArgumentException(Arrays.toString(t));target=t[0];}
        if(args[0].equals("restore"))restore(model,target);else apply(model,target);
        if(args[4].equals("run")){
            SolverSequence sol=model.sol(target.split("/")[0]);long start=System.nanoTime();sol.runAll();
            double seconds=(System.nanoTime()-start)*1e-9;for(double v:sol.getU())if(!Double.isFinite(v))throw new IllegalStateException("Nonfinite solution");
            System.out.println("PANGU_RUN_COMPLETE seconds="+seconds);
        }
        model.save(args[2]);System.out.println("PANGU_CONFIG_COMPLETE target="+target+" output="+args[2]);ModelUtil.remove("panguModel");
    }
}
