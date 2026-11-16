#include <cudss.h>
#include <cuda_runtime_api.h>
#include <complex>
#include <vector>
#include <cmath>
#include <cstdio>
#include <stdexcept>
using Z=std::complex<double>;
static void ck(cudssStatus_t s){if(s)throw std::runtime_error("cuDSS ABI status="+std::to_string((int)s));}
static void cu(cudaError_t s){if(s)throw std::runtime_error(cudaGetErrorString(s));}
struct DeviceBuffer {
 void *p=nullptr;
 DeviceBuffer(bool enabled,const void *source,size_t bytes){if(enabled){cu(cudaMalloc(&p,bytes));cu(cudaMemcpy(p,source,bytes,cudaMemcpyHostToDevice));}}
 ~DeviceBuffer(){if(p)cudaFree(p);}
 void *or_host(void *host){return p?p:host;}
 void update(const void *host,size_t bytes){if(p)cu(cudaMemcpy(p,host,bytes,cudaMemcpyHostToDevice));}
};
template<class V> void test(const char*name,cudssMatrixType_t type,bool lower,bool complex,bool device=false){
 const int n=3,nrhs=2;V dense[9]={V(4),V(1),V(0),V(1),V(5),V(1),V(0),V(1),V(6)};
 if constexpr(std::is_same_v<V,Z>){
  if(type==CUDSS_MTYPE_HPD){dense[1]=Z(1,1);dense[3]=Z(1,-1);dense[5]=Z(1,2);dense[7]=Z(1,-2);}
  else {dense[0]=Z(4,1);dense[3]=Z(2,-1);dense[5]=Z(1,2);}
 }else if(type==CUDSS_MTYPE_GENERAL)dense[3]=V(2);
 std::vector<int> ptr(4),idx;std::vector<V>val,x(6),b(6),truth(6);
 for(int r=0;r<n;r++){ptr[r]=idx.size();for(int c=0;c<n;c++)if((!lower||c<=r)&&std::abs(dense[r*n+c])>0){idx.push_back(c);val.push_back(dense[r*n+c]);}}ptr[n]=idx.size();
 for(int i=0;i<6;i++)truth[i]=V(i+1);
 auto rhs=[&](){for(int j=0;j<nrhs;j++)for(int r=0;r<n;r++){b[j*n+r]=V(0);for(int c=0;c<n;c++)b[j*n+r]+=dense[r*n+c]*truth[j*n+c];}};rhs();
 DeviceBuffer dp(device,ptr.data(),ptr.size()*sizeof(int)),di(device,idx.data(),idx.size()*sizeof(int)),
  dv(device,val.data(),val.size()*sizeof(V)),db(device,b.data(),b.size()*sizeof(V)),dx(device,x.data(),x.size()*sizeof(V));
 cudssHandle_t h; cudssConfig_t config;cudssData_t data;cudssMatrix_t A,B,X;
 ck(cudssCreate(&h));ck(cudssConfigCreate(&config));ck(cudssDataCreate(h,&data));
 cudaDataType_t scalar=complex?CUDA_C_64F:CUDA_R_64F;
 ck(cudssMatrixCreateCsr(&A,n,n,idx.size(),dp.or_host(ptr.data()),nullptr,di.or_host(idx.data()),dv.or_host(val.data()),CUDA_R_32I,scalar,type,lower?CUDSS_MVIEW_LOWER:CUDSS_MVIEW_FULL,CUDSS_BASE_ZERO));
 ck(cudssMatrixCreateDn(&B,n,nrhs,n,db.or_host(b.data()),scalar,CUDSS_LAYOUT_COL_MAJOR));ck(cudssMatrixCreateDn(&X,n,nrhs,n,dx.or_host(x.data()),scalar,CUDSS_LAYOUT_COL_MAJOR));
 ck(cudssExecute(h,CUDSS_PHASE_ANALYSIS,config,data,A,X,B));ck(cudssExecute(h,CUDSS_PHASE_FACTORIZATION,config,data,A,X,B));
 for(int step=0;step<2;step++){
  if(step){for(int r=0;r<n;r++){dense[r*n+r]+=V(2);for(int k=ptr[r];k<ptr[r+1];k++)if(idx[k]==r)val[k]+=V(2);}rhs();dv.update(val.data(),val.size()*sizeof(V));db.update(b.data(),b.size()*sizeof(V));ck(cudssExecute(h,CUDSS_PHASE_REFACTORIZATION,config,data,A,X,B));}
  ck(cudssExecute(h,CUDSS_PHASE_SOLVE,config,data,A,X,B));if(device)cu(cudaMemcpy(x.data(),dx.p,x.size()*sizeof(V),cudaMemcpyDeviceToHost));double err=0;for(int i=0;i<6;i++)err=std::max(err,std::abs(x[i]-truth[i]));
  printf("ADAPTER_CASE name=%s memory=%s step=%d nrhs=%d error=%.17g\n",name,device?"device":"host",step,nrhs,err);if(!std::isfinite(err)||err>1e-10)throw std::runtime_error("Incorrect solution");
 }
 int transpose=1;if(cudssConfigSet(config,CUDSS_CONFIG_SOLVE_MODE,&transpose,sizeof(transpose))!=CUDSS_STATUS_NOT_SUPPORTED)throw std::runtime_error("Transpose must be rejected");
 ck(cudssMatrixDestroy(X));ck(cudssMatrixDestroy(B));ck(cudssMatrixDestroy(A));ck(cudssDataDestroy(h,data));ck(cudssConfigDestroy(config));ck(cudssDestroy(h));
}
int main(){try{for(bool device:{false,true}){test<double>("real-general",CUDSS_MTYPE_GENERAL,false,false,device);test<Z>("complex-general",CUDSS_MTYPE_GENERAL,false,true,device);test<double>("SPD-lower",CUDSS_MTYPE_SPD,true,false,device);test<Z>("HPD-lower",CUDSS_MTYPE_HPD,true,true,device);}puts("ADAPTER_TEST_PASS");return 0;}catch(const std::exception&e){fprintf(stderr,"%s\n",e.what());return 1;}}
