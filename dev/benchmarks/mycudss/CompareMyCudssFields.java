import com.comsol.model.Model;
import com.comsol.model.SolverSequence;
import com.comsol.model.SolverFeature;
import com.comsol.model.XmeshInfoDofs;
import com.comsol.model.util.ModelUtil;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.security.MessageDigest;
import java.util.Arrays;

public final class CompareMyCudssFields {
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

    public static void main(String[] args) throws Exception {
        if(args.length<2)throw new IllegalArgumentException("reference candidate...");
        ModelUtil.initStandalone(false);Model ref=ModelUtil.load("nativeReference",args[0]);SolverSequence rs=ref.sol("sol6");
        double[] frequencies=rs.getPVals();int[][] ri=fieldIndices(rs);String mesh=physicalMeshHash(rs);
        for(int candidate=1;candidate<args.length;candidate++){
            Model model=ModelUtil.load("candidate",args[candidate]);SolverSequence sol=model.sol("sol6");
            if(!Arrays.equals(frequencies,sol.getPVals())||!mesh.equals(physicalMeshHash(sol)))throw new IllegalStateException("Frequency or geometry mismatch");
            int[][] indices=fieldIndices(sol);for(int f=0;f<4;f++)if(indices[f].length!=ri[f].length)throw new IllegalStateException("Field count mismatch");
            boolean all=true;double worst=0,worstMax=0;int count=0;
            for(int i=1;i<=frequencies.length;i++){
                double[] real=sol.getU(i),imag=sol.getUImag(i),rr=rs.getU(i),ii=rs.getUImag(i);
                finite(real);finite(imag);finite(rr);finite(ii);
                for(int f=0;f<4;f++){
                    double error2=0,ref2=0,maxerror=0,peak=0;
                    for(int j=0;j<indices[f].length;j++){
                        int k=indices[f][j],q=ri[f][j];double dr=real[k]-rr[q],di=imag[k]-ii[q];
                        error2+=dr*dr+di*di;ref2+=rr[q]*rr[q]+ii[q]*ii[q];maxerror=Math.max(maxerror,Math.hypot(dr,di));peak=Math.max(peak,Math.hypot(rr[q],ii[q]));
                    }
                    double relative=Math.sqrt(error2/ref2),relativeMax=maxerror/peak;boolean passed=Double.isFinite(relative)&&Double.isFinite(relativeMax)&&relative<=1e-6&&relativeMax<=1e-5;
                    all&=passed;worst=Math.max(worst,relative);worstMax=Math.max(worstMax,relativeMax);count++;
                    System.out.println("NATIVE_FIELD "+candidate+","+frequencies[i-1]+","+FIELDS[f]+","+relative+","+relativeMax+","+passed);
                }
            }
            System.out.println("NATIVE_COMPARE {\"candidate\":"+candidate+",\"fields\":"+count+",\"passed\":"+all+",\"max_relative_L2\":"+worst+",\"max_relative_max\":"+worstMax+"}");System.out.flush();
            ModelUtil.remove("candidate");System.gc();
            if(!all)throw new IllegalStateException("Native comparison failed");
        }
        ModelUtil.remove("nativeReference");System.out.println("NATIVE_COMPARE_COMPLETE");
    }
}
