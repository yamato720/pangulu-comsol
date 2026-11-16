import com.comsol.model.Model;
import com.comsol.model.SolverSequence;
import com.comsol.model.SolverFeature;
import com.comsol.model.XmeshInfoDofs;
import com.comsol.model.util.ModelUtil;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.security.MessageDigest;
import java.util.Arrays;

/** Read-only validation of an already solved S model; no solver execution or model saving. */
public final class SavedSResultCheck {
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
        if(args.length!=2)throw new IllegalArgumentException("saved-model-copy.mph cpu-reference.mph");
        ModelUtil.initStandalone(false);ModelUtil.showProgress(true);
        long start=System.nanoTime();
        Model m=ModelUtil.load("savedCheck",args[0]);
        Model ref=ModelUtil.load("cpuReference",args[1]);
        SolverSequence sol=m.sol("sol6"),rs=ref.sol("sol6");
        double[] frequencies=sol.getPVals(),rf=rs.getPVals();
        if(frequencies==null||frequencies.length==0)throw new IllegalStateException("No saved sol6 frequency solutions");
        double[] expected=new double[59];int at=0;
        for(int f=20000;f<=80000;f+=5000)expected[at++]=f;
        for(int f=82000;f<=120000;f+=2000)expected[at++]=f;
        for(int f=125000;f<=250000;f+=5000)expected[at++]=f;
        boolean complete=Arrays.equals(frequencies,expected);
        String cfd=hash(m.sol("sol1").getU()),mapping=hash(m.sol("sol5").getU());
        boolean sameCFD=cfd.equals(hash(ref.sol("sol1").getU()));
        boolean sameMapping=mapping.equals(hash(ref.sol("sol5").getU()));
        String meshHash=physicalMeshHash(sol);boolean sameMesh=meshHash.equals(physicalMeshHash(rs));
        if(!sameMesh)throw new IllegalStateException("Physical DOF geometry/order differs from reference");
        int[][] indices=fieldIndices(sol),ri=fieldIndices(rs);
        for(int f=0;f<4;f++)if(indices[f].length!=ri[f].length)throw new IllegalStateException("Physical field DOF count differs");
        int[] numbers=new int[frequencies.length];
        for(int i=0;i<frequencies.length;i++){
            for(int j=0;j<rf.length;j++)if(Math.abs(rf[j]-frequencies[i])<1e-7)numbers[i]=j+1;
            if(numbers[i]==0)throw new IllegalStateException("CPU reference lacks frequency "+frequencies[i]);
        }
        SolverFeature stationary=sol.feature("s1");
        String tag=stationary.feature("fc1").getString("linsolver");
        SolverFeature direct=stationary.feature(tag);String properties=gpuProperties(direct);
        String backend=direct.getString("linsolver");
        boolean passed=true;int passedFields=0;double worstL2=0,worstMax=0;int vectorLength=0;
        StringBuilder csv=new StringBuilder("solution_number,frequency_Hz,field,dof_count,solution_L2,peak_absolute,relative_L2_to_reference,relative_max_to_reference,max_absolute_error,passed\n");
        System.out.println("SAVED_S_CHECK_LOADED frequencies="+frequencies.length+" solver="+tag+" backend="+backend);System.out.flush();
        for(int i=1;i<=frequencies.length;i++){
            double[] real=sol.getU(i),imag=sol.getUImag(i),rr=rs.getU(numbers[i-1]),ii=rs.getUImag(numbers[i-1]);
            finite(real);finite(imag);finite(rr);finite(ii);vectorLength=real.length;
            for(int f=0;f<4;f++){
                double norm2=0,peak=0,ref2=0,error2=0,refpeak=0,maxerror=0;
                for(int j=0;j<indices[f].length;j++){
                    int q=indices[f][j],z=ri[f][j];
                    double mag=Math.hypot(real[q],imag[q]),rm=Math.hypot(rr[z],ii[z]),er=Math.hypot(real[q]-rr[z],imag[q]-ii[z]);
                    norm2+=mag*mag;peak=Math.max(peak,mag);ref2+=rm*rm;error2+=er*er;refpeak=Math.max(refpeak,rm);maxerror=Math.max(maxerror,er);
                }
                double rel=Math.sqrt(error2/ref2),relmax=maxerror/refpeak;
                boolean ok=Double.isFinite(rel)&&Double.isFinite(relmax)&&rel<=1e-6&&relmax<=1e-5;
                passed&=ok;if(ok)passedFields++;worstL2=Math.max(worstL2,rel);worstMax=Math.max(worstMax,relmax);
                csv.append(i).append(',').append(frequencies[i-1]).append(',').append(FIELDS[f]).append(',').append(indices[f].length).append(',').append(Math.sqrt(norm2)).append(',').append(peak).append(',').append(rel).append(',').append(relmax).append(',').append(maxerror).append(',').append(ok).append('\n');
            }
            if(i%10==0||i==frequencies.length){System.out.println("SAVED_S_CHECK_PROGRESS "+i+"/"+frequencies.length);System.out.flush();}
        }
        boolean accepted=complete&&sameCFD&&sameMapping&&sameMesh&&passed;
        System.out.println("SAVED_S_FIELDS_CSV_BEGIN");System.out.print(csv);System.out.println("SAVED_S_FIELDS_CSV_END");
        System.out.println("SAVED_S_REPORT_JSON {\"status\":\""+(accepted?"completed":"failed")+"\",\"read_only\":true,\"frequency_count\":"+frequencies.length+",\"expected_frequency_count\":59,\"complete_frequency_list\":"+complete+",\"frequencies_Hz\":"+Arrays.toString(frequencies)+",\"all_solutions_finite\":true,\"CFD_matches_reference\":"+sameCFD+",\"Mapping_matches_reference\":"+sameMapping+",\"physical_mesh_matches_reference\":"+sameMesh+",\"CFD_sha256\":\""+cfd+"\",\"Mapping_sha256\":\""+mapping+"\",\"physical_mesh_sha256\":\""+meshHash+"\",\"solution_vector_length\":"+vectorLength+",\"physical_dofs_per_field\":"+indices[0].length+",\"field_records\":"+(frequencies.length*4)+",\"fields_passed\":"+passedFields+",\"all_fields_passed\":"+passed+",\"worst_relative_L2\":"+worstL2+",\"worst_relative_max\":"+worstMax+",\"linear_solver_tag\":"+quote(tag)+",\"linear_solver_label\":"+quote(direct.label())+",\"linear_solver_backend\":"+quote(backend)+",\"gpu_properties\":"+properties+",\"validation_seconds\":"+((System.nanoTime()-start)*1e-9)+"}");
        System.out.println("SAVED_S_CHECK_COMPLETE");System.out.flush();
        ModelUtil.remove("cpuReference");ModelUtil.remove("savedCheck");
    }
}
