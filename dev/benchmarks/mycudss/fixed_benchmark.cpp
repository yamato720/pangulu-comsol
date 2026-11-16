// Fixed-input macro calibration through the real cuDSS ABI bridge.
#include <cudss.h>
#include <cuda_runtime_api.h>
#include <vector>
#include <complex>
#include <fstream>
#include <iostream>
#include <iomanip>
#include <chrono>
#include <stdexcept>
#include <string>
using Z=std::complex<double>;using L=std::complex<long double>;
static double now(){return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();}
template<class T>static std::vector<T> read(const std::string&p){std::ifstream f(p,std::ios::binary|std::ios::ate);if(!f)throw std::runtime_error(p);auto bytes=f.tellg();std::vector<T>a((size_t)bytes/sizeof(T));f.seekg(0);f.read((char*)a.data(),bytes);if(!f)throw std::runtime_error("read failed");return a;}
static void ok(cudssStatus_t s){if(s)throw std::runtime_error("cuDSS ABI status="+std::to_string(s));}
static void cu(cudaError_t s){if(s)throw std::runtime_error("CUDA status="+std::to_string(s));}
static std::vector<Z> mv(const std::vector<uint64_t>&p,const std::vector<int32_t>&idx,const std::vector<Z>&a,const std::vector<Z>&x){std::vector<L>v(x.size());for(size_t c=0;c<x.size();c++)for(uint64_t k=p[c];k<p[c+1];k++)v[idx[k]]+=L(a[k].real(),a[k].imag())*L(x[c].real(),x[c].imag());std::vector<Z>r(x.size());for(size_t k=0;k<r.size();k++)r[k]=Z(v[k].real(),v[k].imag());return r;}
static double norm(const std::vector<Z>&v){long double sum=0;for(auto x:v)sum+=std::norm(L(x.real(),x.imag()));return sqrt(sum);}
int main(int argc,char**argv){
 if(argc!=3)return 2;
 try{
  std::cout<<std::setprecision(12);std::string root=argv[1];int iterations=std::stoi(argv[2]);
  auto ptr=read<uint64_t>(root+"/previous_ptr.u64");auto idx=read<int32_t>(root+"/previous_idx.i32");
  std::vector<Z> matrices[2]={read<Z>(root+"/previous_values.bin"),read<Z>(root+"/candidate_values.bin")};
  if(ptr!=read<uint64_t>(root+"/candidate_ptr.u64")||idx!=read<int32_t>(root+"/candidate_idx.i32"))throw std::runtime_error("patterns differ");
  size_t n=ptr.size()-1,nz=idx.size();std::vector<int32_t> rp(n+1),ci(nz),next;std::vector<uint64_t> map(nz);
  for(auto row:idx)rp[row+1]++;for(size_t k=0;k<n;k++)rp[k+1]+=rp[k];next=rp;
  for(size_t col=0;col<n;col++)for(uint64_t k=ptr[col];k<ptr[col+1];k++){uint64_t z=next[idx[k]]++;ci[z]=col;map[z]=k;}
  std::vector<Z> exact(n,Z(1,.25)),rhs[2]={mv(ptr,idx,matrices[0],exact),mv(ptr,idx,matrices[1],exact)},csr(nz);
  void *dp,*di,*dv,*db,*dx;cu(cudaMalloc(&dp,rp.size()*4));cu(cudaMalloc(&di,nz*4));cu(cudaMalloc(&dv,nz*16));cu(cudaMalloc(&db,n*16));cu(cudaMalloc(&dx,n*16));cu(cudaMemcpy(dp,rp.data(),rp.size()*4,cudaMemcpyHostToDevice));cu(cudaMemcpy(di,ci.data(),nz*4,cudaMemcpyHostToDevice));
  cudssHandle_t h=nullptr;cudssConfig_t config=nullptr;cudssData_t data=nullptr;cudssMatrix_t A=nullptr,B=nullptr,X=nullptr;
  ok(cudssCreate(&h));ok(cudssConfigCreate(&config));ok(cudssDataCreate(h,&data));int zero=0;double threshold=1,epsilon=1e-13;ok(cudssConfigSet(config,CUDSS_CONFIG_HYBRID_EXECUTE_MODE,&zero,4));ok(cudssConfigSet(config,CUDSS_CONFIG_REORDERING_ALG,&zero,4));ok(cudssConfigSet(config,CUDSS_CONFIG_FACTORIZATION_ALG,&zero,4));ok(cudssConfigSet(config,CUDSS_CONFIG_PIVOT_THRESHOLD,&threshold,8));ok(cudssConfigSet(config,CUDSS_CONFIG_PIVOT_EPSILON,&epsilon,8));
  ok(cudssMatrixCreateCsr(&A,n,n,nz,dp,nullptr,di,dv,CUDA_R_32I,CUDA_C_64F,CUDSS_MTYPE_GENERAL,CUDSS_MVIEW_FULL,CUDSS_BASE_ZERO));
  ok(cudssMatrixCreateDn(&B,n,1,n,db,CUDA_C_64F,CUDSS_LAYOUT_COL_MAJOR));ok(cudssMatrixCreateDn(&X,n,1,n,dx,CUDA_C_64F,CUDSS_LAYOUT_COL_MAJOR));
  std::vector<std::vector<Z>> results;results.reserve(iterations*2);
  for(int iteration=0;iteration<iterations;iteration++){
   for(size_t k=0;k<nz;k++)csr[k]=matrices[iteration%2][map[k]];
   cu(cudaMemcpy(dv,csr.data(),nz*16,cudaMemcpyHostToDevice));
   double start=now();ok(cudssExecute(h,CUDSS_PHASE_REORDERING,config,data,A,nullptr,nullptr));ok(cudssExecute(h,CUDSS_PHASE_SYMBOLIC_FACTORIZATION,config,data,A,nullptr,nullptr));ok(cudssExecute(h,CUDSS_PHASE_FACTORIZATION,config,data,A,nullptr,nullptr));cu(cudaDeviceSynchronize());double factor=now()-start;
   double solve=0;
   for(int right=0;right<2;right++){
    auto b=rhs[iteration%2];if(right)for(auto&z:b)z*=1e-14;cu(cudaMemcpy(db,b.data(),n*16,cudaMemcpyHostToDevice));
    start=now();ok(cudssExecute(h,CUDSS_PHASE_SOLVE,config,data,A,X,B));cu(cudaDeviceSynchronize());solve+=now()-start;
    results.emplace_back(n);cu(cudaMemcpy(results.back().data(),dx,n*16,cudaMemcpyDeviceToHost));
   }
   std::cout<<"BRIDGE_ITERATION {\"iteration\":"<<iteration<<",\"factor_callbacks_seconds\":"<<factor<<",\"solve_callbacks_seconds\":"<<solve<<",\"seconds\":"<<factor+solve<<"}"<<std::endl;
  }
  ok(cudssMatrixDestroy(A));ok(cudssMatrixDestroy(B));ok(cudssMatrixDestroy(X));ok(cudssDataDestroy(h,data));ok(cudssConfigDestroy(config));ok(cudssDestroy(h));
  cudaFree(dp);cudaFree(di);cudaFree(dv);cudaFree(db);cudaFree(dx);
  bool passed=true;double worst=0;
  for(int iteration=0;iteration<iterations;iteration++)for(int right=0;right<2;right++){
   auto expected=exact,b=rhs[iteration%2];if(right){for(auto&z:expected)z*=1e-14;for(auto&z:b)z*=1e-14;}
   auto &x=results[2*iteration+right];auto residual=mv(ptr,idx,matrices[iteration%2],x);auto difference=x;
   for(size_t k=0;k<n;k++){residual[k]-=b[k];difference[k]-=expected[k];}
   double rr=norm(residual)/norm(b),error=norm(difference)/norm(expected);bool good=std::isfinite(rr)&&std::isfinite(error)&&rr<=1e-8&&error<=1e-6;passed&=good;worst=std::max(worst,error);
   std::cout<<"CHECK {\"iteration\":"<<iteration<<",\"rhs\":"<<right<<",\"relative_residual\":"<<rr<<",\"relative_x_error\":"<<error<<",\"passed\":"<<(good?"true":"false")<<"}"<<std::endl;
  }
  std::cout<<"RESULT {\"passed\":"<<(passed?"true":"false")<<",\"maximum_relative_x_error\":"<<worst<<"}"<<std::endl;return passed?0:2;
 }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}
}
