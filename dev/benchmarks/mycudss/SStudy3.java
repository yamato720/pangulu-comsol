import com.comsol.model.Model;
import com.comsol.model.SolverSequence;
import com.comsol.model.SolverFeature;
import com.comsol.model.XmeshInfoDofs;
import com.comsol.model.util.ModelUtil;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.security.MessageDigest;
import java.util.Arrays;

/** Native COMSOL cuDSS sweep. Configuration and post-solve validation are outside the timer. */
public final class SStudy3 {
    private static final String[] FIELDS={"comp1.p2","comp1.u2","comp1.v2","comp1.T2"};
    private static String hex(byte[] bytes) {
        StringBuilder s=new StringBuilder();for(byte x:bytes)s.append(String.format("%02x",x&255));return s.toString();
    }
    private static String hash(double[] a) throws Exception {
        MessageDigest md=MessageDigest.getInstance("SHA-256");ByteBuffer b=ByteBuffer.allocate(65536).order(ByteOrder.LITTLE_ENDIAN);
        for(double x:a){if(!Double.isFinite(x))throw new IllegalStateException("Nonfinite solution");if(b.remaining()<8){md.update(b.array(),0,b.position());b.clear();}b.putLong(Double.doubleToRawLongBits(x));}
        md.update(b.array(),0,b.position());return hex(md.digest());
    }
    private static int[] names(XmeshInfoDofs dofs) {
        String[] n=dofs.dofNames();int[] which=new int[n.length];Arrays.fill(which,-1);
        for(int i=0;i<n.length;i++)for(int f=0;f<FIELDS.length;f++)if(n[i].equals(FIELDS[f]))which[i]=f;
        return which;
    }
    private static int[][] fieldIndices(SolverSequence sol) {
        XmeshInfoDofs d=sol.xmeshInfo().dofs();int[] which=names(d),ni=d.nameInds(),si=d.solVectorInds(),counts=new int[4];
        for(int k:ni)if(which[k]>=0)counts[which[k]]++;
        int[][] out=new int[4][];for(int f=0;f<4;f++){if(counts[f]==0)throw new IllegalStateException("Missing field "+FIELDS[f]);out[f]=new int[counts[f]];}
        Arrays.fill(counts,0);for(int k=0;k<ni.length;k++)if(which[ni[k]]>=0){int f=which[ni[k]];out[f][counts[f]++]=si[k];}
        return out;
    }
    private static String physicalMeshHash(SolverSequence sol) throws Exception {
        XmeshInfoDofs d=sol.xmeshInfo().dofs();int[] which=names(d),ni=d.nameInds(),nodes=d.nodes(),geom=d.geomNums();double[][] c=d.coords();
        MessageDigest md=MessageDigest.getInstance("SHA-256");ByteBuffer b=ByteBuffer.allocate(12+8*c.length).order(ByteOrder.LITTLE_ENDIAN);
        for(int k=0;k<ni.length;k++)if(which[ni[k]]>=0){b.clear();b.putInt(which[ni[k]]).putInt(nodes[k]).putInt(geom[k]);for(double[] row:c)b.putDouble(row[k]);md.update(b.array());}
        return hex(md.digest());
    }
    private static void finite(double[] a){for(double x:a)if(!Double.isFinite(x))throw new IllegalStateException("Nonfinite solution");}
    private static void noPlots(SolverFeature f) {
        try{f.set("plot","off");}catch(Exception ignored){}
        for(String tag:f.feature().tags())noPlots(f.feature(tag));
    }
    private static String quote(String s) {
        return "\""+s.replace("\\","\\\\").replace("\"","\\\"").replace("\n","\\n").replace("\r","\\r").replace("\t","\\t")+"\"";
    }
    private static String gpuProperties(SolverFeature f) {
        String[] keys=f.properties();Arrays.sort(keys);StringBuilder b=new StringBuilder("{");int count=0;
        for(String key:keys)if(key.startsWith("cudss")||key.equals("linsolver")) {
            String value;try{value=f.getString(key);}catch(Exception e){value="<not-scalar>";}
            if(count++>0)b.append(',');b.append(quote(key)).append(':').append(quote(value));
        }
        return b.append('}').toString();
    }
    public static void main(String[] args) throws Exception {
        System.out.println("BRIDGE_HELPER_ARGUMENT_COUNT "+args.length);System.out.flush();
        try { run(args); }
        catch(Exception|Error e) { e.printStackTrace(System.err);System.err.flush();throw e; }
    }
    private static void run(String[] args) throws Exception {
        if(args.length!=8)throw new IllegalArgumentException("source output reference frequencies-Hz-csv memory-MB custom-cudss-root custom-cuda-root backend");
        String backend=args[7];
        if(!backend.equals("pangu")&&!backend.equals("cudss"))throw new IllegalArgumentException("Unknown backend "+backend);
        String[] requested=args[3].split(",");double[] expected=new double[requested.length];StringBuilder list=new StringBuilder();
        for(int i=0;i<requested.length;i++){expected[i]=Double.parseDouble(requested[i]);if(i>0)list.append(" ");list.append(expected[i]/1000.0);}
        int memoryMB=Integer.parseInt(args[4]);
        if(memoryMB!=0&&memoryMB<1024)throw new IllegalArgumentException("GPU memory budget must be zero or >=1024 MB");
        long javaStart=System.nanoTime();ModelUtil.initStandalone(false);ModelUtil.showProgress(true);
        // The batch method-execution API does not implement setPreference.
        // The launcher supplies these GPU settings via this run's -prefsdir.
        long t=System.nanoTime();Model m=ModelUtil.load("bridgeSweep",args[0]);double loadSeconds=(System.nanoTime()-t)*1e-9;
        t=System.nanoTime();String cfd=hash(m.sol("sol1").getU()),mapping=hash(m.sol("sol5").getU());
        SolverSequence sol=m.sol("sol6");
        if(!"i1".equals(sol.feature("s1").feature("fc1").getString("linsolver")))throw new IllegalStateException("Expected unchanged CPU source model");
        if(!"mumps".equals(sol.feature("s1").feature("i1").feature("dp1").getString("linsolver")))throw new IllegalStateException("Original CPU preconditioner differs");
        String originalList=m.study("std3").feature("freq").getString("plist");
        if(!"range(20,5,80) range(82,2,120) range(125,5,250)".equals(originalList))throw new IllegalStateException("Original frequency list differs");
        SolverFeature stationary=sol.feature("s1");
        if(memoryMB!=0)throw new IllegalArgumentException("Use zero: explicit GPU memory limits are disabled for this matched trial");
        String gpuTag;
        if(backend.equals("pangu"))gpuTag=org.pangulu.comsol.PanguModule.apply(m,"sol6/s1/fc1");
        else{
            gpuTag="nativeCudss";
            SolverFeature direct=stationary.create(gpuTag,"Direct");direct.label("NVIDIA cuDSS");
            direct.set("linsolver","cudss");direct.set("cudssprecision","double");
            String hybrid=Arrays.asList(direct.properties()).contains("cudsshybridmemory")?"cudsshybridmemory":"cudssusehybridmemory";
            direct.set(hybrid,"never");direct.set("cudssusehybridcompute","off");direct.set("cudssreusereorder","on");
            stationary.feature("fc1").set("linsolver",gpuTag);
        }
        SolverFeature gpu=stationary.feature(gpuTag);
        for(String f:sol.feature().tags())noPlots(sol.feature(f));
        if(!gpuTag.equals(stationary.feature("fc1").getString("linsolver"))||!"cudss".equals(gpu.getString("linsolver")))throw new IllegalStateException("GPU solver configuration did not persist");
        m.study("std3").feature("freq").set("plist",list.toString());
        stationary.feature("p1").set("plistarr",new String[]{list.toString()});stationary.feature("p1").set("punit",new String[]{"kHz"});
        String gpuProperties=gpuProperties(gpu);
        String hybridKey=Arrays.asList(gpu.properties()).contains("cudsshybridmemory")?"cudsshybridmemory":"cudssusehybridmemory";
        if(!"never".equals(gpu.getString(hybridKey)))throw new IllegalStateException("GPU memory policy differs");
        System.out.println("S_BACKEND backend="+backend+" target=sol6/s1/fc1 tag="+gpuTag);
        sol.clearSolution();m.save(args[1]+"/configured_copy.mph");
        double precheckSeconds=(System.nanoTime()-t)*1e-9;
        System.out.println("BRIDGE_CONFIG_JSON "+gpuProperties);System.out.flush();
        System.out.println("BRIDGE_SWEEP_BEGIN original_plist="+m.study("std3").feature("freq").getString("plist"));System.out.flush();
        long start=System.nanoTime();sol.runAll();long solveEnd=System.nanoTime();double seconds=(solveEnd-start)*1e-9;
        System.out.println("BRIDGE_SWEEP_CLOCK_JSON {\"begin_ns\":"+start+",\"end_ns\":"+solveEnd+"}");
        System.out.println("BRIDGE_SWEEP_SOLVE_COMPLETE seconds="+seconds);System.out.flush();
        t=System.nanoTime();m.save(args[1]+"/Bridge_solved.mph");double saveSeconds=(System.nanoTime()-t)*1e-9;
        System.out.println("BRIDGE_SWEEP_MODEL_SAVED seconds="+saveSeconds);System.out.flush();
        t=System.nanoTime();double[] frequencies=sol.getPVals();
        if(!Arrays.equals(frequencies,expected))throw new IllegalStateException("Frequency list differs");
        if(!cfd.equals(hash(m.sol("sol1").getU()))||!mapping.equals(hash(m.sol("sol5").getU())))throw new IllegalStateException("Stored background changed");
        int[][] indices=fieldIndices(sol);String meshHash=physicalMeshHash(sol);
        int solutionVectorLength=sol.getU(1).length;
        System.out.println("S_VECTOR_LENGTH "+solutionVectorLength);
        Model ref=null;int[][] ri=null;SolverSequence rs=null;int[] referenceNumbers=new int[expected.length];
        if(args.length>=3&&!args[2].isEmpty()){
            ref=ModelUtil.load("reference",args[2]);rs=ref.sol("sol6");
            double[] rf=rs.getPVals();for(int i=0;i<expected.length;i++){for(int j=0;j<rf.length;j++)if(Math.abs(rf[j]-expected[i])<1e-7)referenceNumbers[i]=j+1;if(referenceNumbers[i]==0)throw new IllegalStateException("Reference frequency missing");}
            if(!cfd.equals(hash(ref.sol("sol1").getU()))||!mapping.equals(hash(ref.sol("sol5").getU())))throw new IllegalStateException("Reference background differs");
            if(!meshHash.equals(physicalMeshHash(rs)))throw new IllegalStateException("Physical DOF geometry/order differs");
            ri=fieldIndices(rs);for(int f=0;f<4;f++)if(ri[f].length!=indices[f].length)throw new IllegalStateException("Reference field DOF count differs");
        }
        boolean passed=true;double worstL2=0,worstMax=0;
        StringBuilder csv=new StringBuilder("solution_number,frequency_Hz,field,dof_count,solution_L2,peak_absolute,relative_L2_to_reference,relative_max_to_reference,max_absolute_error,passed\n");
        for(int i=1;i<=expected.length;i++){
            double[] real=sol.getU(i),imag=sol.getUImag(i);finite(real);finite(imag);
            double[] rr=rs==null?null:rs.getU(referenceNumbers[i-1]),ii=rs==null?null:rs.getUImag(referenceNumbers[i-1]);
            for(int f=0;f<4;f++){
                double norm2=0,peak=0,ref2=0,error2=0,refpeak=0,maxerror=0;
                for(int j=0;j<indices[f].length;j++){
                    int q=indices[f][j];double mag=Math.hypot(real[q],imag[q]);norm2+=mag*mag;peak=Math.max(peak,mag);
                    if(rs!=null){int z=ri[f][j];double rm=Math.hypot(rr[z],ii[z]),er=Math.hypot(real[q]-rr[z],imag[q]-ii[z]);if(!Double.isFinite(rm))throw new IllegalStateException("Nonfinite reference");ref2+=rm*rm;error2+=er*er;refpeak=Math.max(refpeak,rm);maxerror=Math.max(maxerror,er);}
                }
                double rel=rs==null?0:Math.sqrt(error2/ref2),relmax=rs==null?0:maxerror/refpeak;
                boolean ok=rs==null||(Double.isFinite(rel)&&Double.isFinite(relmax)&&rel<=1e-6&&relmax<=1e-5);passed&=ok;worstL2=Math.max(worstL2,rel);worstMax=Math.max(worstMax,relmax);
                csv.append(i).append(',').append(frequencies[i-1]).append(',').append(FIELDS[f]).append(',').append(indices[f].length).append(',').append(Math.sqrt(norm2)).append(',').append(peak).append(',');
                if(rs!=null)csv.append(rel).append(',').append(relmax).append(',').append(maxerror).append(',').append(ok);else csv.append(",,,");csv.append('\n');
            }
        }
        System.out.println("BRIDGE_SWEEP_FIELDS_CSV_BEGIN");System.out.print(csv);System.out.println("BRIDGE_SWEEP_FIELDS_CSV_END");System.out.flush();
        if(ref!=null)ModelUtil.remove("reference");double validationSeconds=(System.nanoTime()-t)*1e-9;
        System.out.println("BRIDGE_SWEEP_REPORT_JSON {\"status\":\"completed\",\"frequency_count\":"+expected.length+",\"frequencies_Hz\":"+Arrays.toString(frequencies)+",\"runAll_seconds\":"+seconds+",\"load_seconds\":"+loadSeconds+",\"precheck_seconds\":"+precheckSeconds+",\"validation_seconds\":"+validationSeconds+",\"save_seconds\":"+saveSeconds+",\"java_total_seconds\":"+((System.nanoTime()-javaStart)*1e-9)+",\"all_solutions_finite\":true,\"CFD_unchanged\":true,\"Mapping_unchanged\":true,\"CFD_sha256\":\""+cfd+"\",\"Mapping_sha256\":\""+mapping+"\",\"physical_mesh_sha256\":\""+meshHash+"\",\"acoustic_dofs_per_field\":"+indices[0].length+",\"mesh_vertices\":"+m.component("comp1").mesh("meshS").stat().getNumVertex()+",\"mesh_elements\":"+m.component("comp1").mesh("meshS").stat().getNumElem()+",\"reference_compared\":"+(rs!=null)+",\"reference_all_fields_passed\":"+(rs!=null?Boolean.toString(passed):"null")+",\"reference_worst_relative_L2\":"+(rs!=null?Double.toString(worstL2):"null")+",\"reference_worst_relative_max\":"+(rs!=null?Double.toString(worstMax):"null")+",\"gpu_properties\":"+gpuProperties+",\"solver\":\"COMSOL Direct via selected external cuDSS ABI library (double)\",\"instrumentation\":\"External cuDSS ABI library; no GDB\",\"timing_scope\":\"sol6.runAll: assembly, native solver setup, requested frequencies; excludes loading, validation, saving\"}");
        if(!passed)throw new IllegalStateException("CPU reference field comparison failed");
        System.out.println("BRIDGE_SWEEP_COMPLETE");System.out.flush();ModelUtil.remove("bridgeSweep");
    }
}
