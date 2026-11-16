#include <cudss.h>
#include <cuda_runtime_api.h>
#include <complex>
#include <vector>
#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <algorithm>
using Z=std::complex<double>;
static void ok(cudssStatus_t s,const char*stage){if(s!=CUDSS_STATUS_SUCCESS){fprintf(stderr,"ABI_SMOKE_FAILURE stage=%s status=%d\n",stage,s);exit(2);}}
static void cu(cudaError_t s,const char*stage){if(s!=cudaSuccess){fprintf(stderr,"ABI_SMOKE_CUDA_FAILURE stage=%s status=%d\n",stage,(int)s);exit(3);}}
static void trial(bool device_input){
 int64_t ptr[]={0,2,4,7},idx[]={0,1,1,2,0,1,2};
 std::vector<Z> values={Z(4,1),Z(1,-1),Z(5,.2),Z(.5,.3),Z(1,.5),Z(-1,2),Z(6,-.1)};
 const std::vector<Z> exact={Z(1,2),Z(-.5,.7),Z(3,-1)};
 void *dp=nullptr,*di=nullptr,*dv=nullptr,*db=nullptr,*dx=nullptr;
 cu(cudaMalloc(&db,3*sizeof(Z)),"malloc b");cu(cudaMalloc(&dx,3*sizeof(Z)),"malloc x");
 if(device_input){cu(cudaMalloc(&dp,sizeof(ptr)),"malloc ptr");cu(cudaMalloc(&di,sizeof(idx)),"malloc idx");cu(cudaMalloc(&dv,values.size()*sizeof(Z)),"malloc val");cu(cudaMemcpy(dp,ptr,sizeof(ptr),cudaMemcpyHostToDevice),"copy ptr");cu(cudaMemcpy(di,idx,sizeof(idx),cudaMemcpyHostToDevice),"copy idx");}
 cudssHandle_t h=nullptr;cudssConfig_t config=nullptr;cudssData_t data=nullptr;cudssMatrix_t A=nullptr,B=nullptr,X=nullptr;
 ok(cudssCreate(&h),"create");ok(cudssConfigCreate(&config),"config create");ok(cudssDataCreate(h,&data),"data create");
 ok(cudssMatrixCreateDn(&B,3,1,3,db,CUDA_C_64F,CUDSS_LAYOUT_COL_MAJOR),"B create");ok(cudssMatrixCreateDn(&X,3,1,3,dx,CUDA_C_64F,CUDSS_LAYOUT_COL_MAJOR),"X create");
 double worst=0,worst_residual=0;
 for(int pass=0;pass<2;pass++){
  if(pass){values[0]+=Z(1,.1);values[2]+=Z(2,-.5);}
  std::vector<Z>b(3),got(3);for(int i=0;i<3;i++)for(int64_t k=ptr[i];k<ptr[i+1];k++)b[i]+=values[k]*exact[idx[k]];
  if(device_input)cu(cudaMemcpy(dv,values.data(),values.size()*sizeof(Z),cudaMemcpyHostToDevice),"copy values");
  if(A)ok(cudssMatrixDestroy(A),"old A destroy");
  ok(cudssMatrixCreateCsr(&A,3,3,7,device_input?dp:ptr,nullptr,device_input?di:idx,device_input?dv:values.data(),CUDA_R_64I,CUDA_C_64F,CUDSS_MTYPE_GENERAL,CUDSS_MVIEW_FULL,CUDSS_BASE_ZERO),"A create");
  cu(cudaMemcpy(db,b.data(),3*sizeof(Z),cudaMemcpyHostToDevice),"copy b");
  if(pass==0)ok(cudssExecute(h,CUDSS_PHASE_ANALYSIS,config,data,A,nullptr,nullptr),"analysis");
  ok(cudssExecute(h,pass?CUDSS_PHASE_REFACTORIZATION:CUDSS_PHASE_FACTORIZATION,config,data,A,nullptr,nullptr),"factorization");
  ok(cudssExecute(h,CUDSS_PHASE_SOLVE,config,data,A,X,B),"solve");
  cu(cudaMemcpy(got.data(),dx,3*sizeof(Z),cudaMemcpyDeviceToHost),"get x");
  double r2=0,b2=0;for(int i=0;i<3;i++){worst=std::max(worst,std::abs(got[i]-exact[i]));Z ax=0.;for(int64_t k=ptr[i];k<ptr[i+1];k++)ax+=values[k]*got[idx[k]];r2+=std::norm(ax-b[i]);b2+=std::norm(b[i]);}worst_residual=std::max(worst_residual,std::sqrt(r2/b2));
  int info=-99;size_t written=0;ok(cudssDataGet(h,data,CUDSS_DATA_INFO,&info,sizeof(info),&written),"info");if(info||written!=4)exit(4);
 }
 std::vector<Z> zero(3),zero_x(3);cu(cudaMemcpy(db,zero.data(),3*sizeof(Z),cudaMemcpyHostToDevice),"zero rhs");
 ok(cudssExecute(h,CUDSS_PHASE_SOLVE,config,data,A,X,B),"repeat zero-RHS solve");cu(cudaMemcpy(zero_x.data(),dx,3*sizeof(Z),cudaMemcpyDeviceToHost),"zero get x");
 for(auto z:zero_x)if(z!=Z(0,0)){fprintf(stderr,"ABI_SMOKE_FAILURE zero RHS did not overwrite the previous x\n");exit(6);}
 ok(cudssMatrixDestroy(A),"A destroy");ok(cudssMatrixDestroy(B),"B destroy");ok(cudssMatrixDestroy(X),"X destroy");ok(cudssDataDestroy(h,data),"data destroy");ok(cudssConfigDestroy(config),"config destroy");ok(cudssDestroy(h),"destroy");
 cudaFree(db);cudaFree(dx);if(dp)cudaFree(dp);if(di)cudaFree(di);if(dv)cudaFree(dv);
 printf("ABI_SMOKE_RESULT {\"device_input\":%s,\"backend\":\"selected_cudss_backend\",\"refactor_passes\":2,\"max_absolute_x_error\":%.17g,\"worst_relative_residual\":%.17g,\"passed\":%s}\n",device_input?"true":"false",worst,worst_residual,worst<1e-12&&worst_residual<1e-12?"true":"false");
 if(worst>=1e-12||worst_residual>=1e-12)exit(5);
}
int main(){trial(true);return 0;}
