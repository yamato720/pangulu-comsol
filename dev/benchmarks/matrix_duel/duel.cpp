#include "activity.h"
#include <cuda_runtime_api.h>
#if BENCH_PANGU
#include <pangulu.h>
#else
#include <cudss.h>
#endif
#include <vector>
#include <complex>
#include <fstream>
#include <iostream>
#include <iomanip>
#include <stdexcept>
#include <string>
#include <chrono>
#include <cmath>
#include <algorithm>
#include <ctime>
#include <type_traits>
#include <unistd.h>
#include <link.h>
#include <cstring>
#include <cstdlib>
using Z=std::complex<double>;using L=std::complex<long double>;
static uint64_t stamp(clockid_t c){timespec t;clock_gettime(c,&t);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static void cu(cudaError_t s){if(s)throw std::runtime_error("CUDA "+std::to_string((int)s)+" "+cudaGetErrorString(s));}
template<class T>std::vector<T>load(const std::string&file){std::ifstream f(file,std::ios::binary|std::ios::ate);if(!f)throw std::runtime_error(file);size_t bytes=f.tellg();if(bytes%sizeof(T))throw std::runtime_error("bad input width");std::vector<T>v(bytes/sizeof(T));f.seekg(0);f.read((char*)v.data(),bytes);if(!f)throw std::runtime_error("read failed");return v;}
struct Device{void*p=nullptr;~Device(){if(p)cudaFree(p);}void allocate(size_t n){cu(cudaMalloc(&p,n));}void upload(const void*src,size_t n){cu(cudaMemcpy(p,src,n,cudaMemcpyHostToDevice));}};
template<class T>struct Wide{using Type=long double;static Type get(T x){return x;}};
template<>struct Wide<Z>{using Type=L;static L get(Z x){return L(x.real(),x.imag());}};
template<class T>std::vector<T>mv(const std::vector<uint64_t>&ptr,const std::vector<int32_t>&idx,const std::vector<T>&v,const std::vector<T>&x,int nrhs){
 size_t n=ptr.size()-1;using W=typename Wide<T>::Type;std::vector<W>acc(n*nrhs);
 for(int j=0;j<nrhs;j++)for(size_t col=0;col<n;col++){W a=Wide<T>::get(x[j*n+col]);for(uint64_t k=ptr[col];k<ptr[col+1];k++)acc[j*n+idx[k]]+=Wide<T>::get(v[k])*a;}
 std::vector<T>out(acc.size());for(size_t i=0;i<out.size();i++){if constexpr(std::is_same_v<T,Z>)out[i]=Z(acc[i].real(),acc[i].imag());else out[i]=(T)acc[i];}return out;
}
template<class T>long double norm2(const std::vector<T>&v){long double s=0;for(auto x:v){auto a=Wide<T>::get(x);if constexpr(std::is_same_v<T,Z>)s+=std::norm(a);else s+=a*a;}return s;}
static void require(int s,const char*stage){if(s)throw std::runtime_error(std::string(stage)+" status="+std::to_string(s));}
static int loaded(dl_phdr_info *info,size_t,void*){const char *p=info->dlpi_name;if(p&&(strstr(p,"libpangulu")||strstr(p,"libcudss")||strstr(p,"libcudart")||strstr(p,"libcublas")||strstr(p,"libcupti")))std::cout<<"LOADED "<<p<<std::endl;return 0;}

template<class T>void run(const std::string&root,bool spd,int refactors,bool csr_input){
 const uint64_t prep_begin=stamp(CLOCK_MONOTONIC);
 const bool single_values=std::getenv("PC_MATRIX_SINGLE_VALUES")&&std::string(std::getenv("PC_MATRIX_SINGLE_VALUES"))=="1";
 auto cp=load<uint64_t>(root+"/ptr.u64");auto ri=load<int32_t>(root+"/idx.i32");auto original=load<T>(root+"/values.bin");
 std::vector<T>physical_rhs;
 if(std::ifstream(root+"/physical_rhs.bin",std::ios::binary).good())physical_rhs=load<T>(root+"/physical_rhs.bin");
 size_t n=cp.size()-1,nz=ri.size();if(cp.front()!=0||cp.back()!=nz||original.size()!=nz||n>INT32_MAX||nz>INT32_MAX)throw std::runtime_error("invalid CSC");
 for(auto value:original)if(!std::isfinite(std::abs(value)))throw std::runtime_error("nonfinite matrix input");
 for(size_t col=0;col<n;col++){int previous=-1;if(cp[col]>cp[col+1])throw std::runtime_error("invalid pointers");for(uint64_t k=cp[col];k<cp[col+1];k++){if(ri[k]<=previous||ri[k]<0||(size_t)ri[k]>=n)throw std::runtime_error("CSC indices not sorted/unique");previous=ri[k];}}
 std::vector<int32_t>rp(n+1),ci(nz),next;std::vector<uint64_t>to_csc(nz);
 for(auto row:ri)rp[row+1]++;for(size_t i=0;i<n;i++)rp[i+1]+=rp[i];next=rp;
 for(size_t col=0;col<n;col++)for(uint64_t k=cp[col];k<cp[col+1];k++){size_t z=next[ri[k]]++;ci[z]=col;to_csc[z]=k;}
 std::vector<uint64_t>rp64(rp.begin(),rp.end());
 if(!physical_rhs.empty()&&physical_rhs.size()!=n)throw std::runtime_error("physical RHS size mismatch");
 const int max_rhs=std::getenv("PC_MATRIX_MAX_RHS")?std::stoi(std::getenv("PC_MATRIX_MAX_RHS")):8;
 if(max_rhs<1||max_rhs>8)throw std::runtime_error("unsupported RHS column count");
 std::vector<T>truth(n*max_rhs);
 for(int j=0;j<max_rhs;j++)for(size_t i=0;i<n;i++){double a=1+.1*j+.001*(i%17);if constexpr(std::is_same_v<T,Z>)truth[j*n+i]=Z(a,.15+.01*j+.0003*(i%11));else truth[j*n+i]=a;}
 const double scales[3]={1.,1.01,.99};std::vector<T>values[3],rhs[3],device_values[3];
 for(int v=0;v<3;v++){
  values[v]=original;for(auto&x:values[v])x*=scales[v];rhs[v]=mv(cp,ri,values[v],truth,max_rhs);
  device_values[v].resize(nz);
#if BENCH_PANGU
  if(!csr_input)device_values[v]=values[v];else
#endif
  for(size_t k=0;k<nz;k++)device_values[v][k]=values[v][to_csc[k]];
 }
 std::cout<<"MATRIX {\"n\":"<<n<<",\"nnz\":"<<nz<<",\"complex\":"<<(std::is_same_v<T,Z>?"true":"false")<<",\"spd\":"<<(spd?"true":"false")<<",\"host_preparation_seconds\":"<<(stamp(CLOCK_MONOTONIC)-prep_begin)*1e-9<<"}"<<std::endl;
#if BENCH_PANGU
 pangulu_handle_t h=nullptr;pangulu_matrix_t A=nullptr;pangulu_vector_t B=nullptr,X=nullptr;
 const char*order=std::getenv("PC_MATRIX_REORDER");bool l1=order&&std::string(order)=="L1";
 if(order&&std::string(order)!="L0"&&!l1)throw std::runtime_error("unsupported reorder setting");
 require(pangulu_create(0,&h),"create");require(pangulu_config_set(h,PANGULU_REORDERING_METHOD,l1?L1:L0),"ordering");require(pangulu_config_set(h,PANGULU_REUSE_LEVEL,L2),"L2");
 std::cout<<"CONFIG {\"reorder\":\""<<(l1?"L1":"L0")<<"\",\"reuse\":\"L2\"}"<<std::endl;
 constexpr pangulu_value_type_t dtype=std::is_same_v<T,Z>?PANGULU_C_64F:PANGULU_R_64F;
#else
 cudssHandle_t h=nullptr;cudssConfig_t config=nullptr;cudssData_t data=nullptr;cudssMatrix_t A=nullptr,B=nullptr,X=nullptr;
 cu(cudaSetDevice(0));require(cudssCreate(&h),"create");require(cudssConfigCreate(&config),"config");require(cudssDataCreate(h,&data),"data");
 int zero=0;double threshold=1,epsilon=1e-13;
 require(cudssConfigSet(config,CUDSS_CONFIG_HYBRID_EXECUTE_MODE,&zero,4),"hybrid off");require(cudssConfigSet(config,CUDSS_CONFIG_REORDERING_ALG,&zero,4),"reorder auto");require(cudssConfigSet(config,CUDSS_CONFIG_FACTORIZATION_ALG,&zero,4),"factor auto");
 require(cudssConfigSet(config,CUDSS_CONFIG_PIVOT_THRESHOLD,&threshold,8),"pivot");require(cudssConfigSet(config,CUDSS_CONFIG_PIVOT_EPSILON,&epsilon,8),"epsilon");
 constexpr cudaDataType_t dtype=std::is_same_v<T,Z>?CUDA_C_64F:CUDA_R_64F;
#endif
 int ordinal=-1;cu(cudaGetDevice(&ordinal));cudaDeviceProp prop{};cu(cudaGetDeviceProperties(&prop,ordinal));
 std::cout<<"DEVICE {\"ordinal\":"<<ordinal<<",\"name\":\""<<prop.name<<"\",\"pid\":"<<getpid()<<"}"<<std::endl;
 Device dp,di,dval[3],db[3],dx,physical_b;
#if BENCH_PANGU
 dp.allocate((n+1)*8);dp.upload(csr_input?(void*)rp64.data():(void*)cp.data(),(n+1)*8);di.allocate(nz*4);di.upload(csr_input?ci.data():ri.data(),nz*4);
#else
 dp.allocate((n+1)*4);dp.upload(rp.data(),(n+1)*4);di.allocate(nz*4);di.upload(ci.data(),nz*4);
#endif
 for(int v=0;v<3;v++){if(!single_values||v==0){dval[v].allocate(nz*sizeof(T));dval[v].upload(device_values[v].data(),nz*sizeof(T));}db[v].allocate(n*max_rhs*sizeof(T));db[v].upload(rhs[v].data(),n*max_rhs*sizeof(T));}
 dx.allocate(n*max_rhs*sizeof(T));
 if(!physical_rhs.empty()){physical_b.allocate(n*sizeof(T));physical_b.upload(physical_rhs.data(),n*sizeof(T));}
 cu(cudaDeviceSynchronize());
 uint64_t id=0;std::vector<std::vector<T>>solutions;std::vector<std::pair<int,int>>solution_info;
 auto phase=[&](const char*name,int iteration,int nrhs,auto operation){
  cu(cudaDeviceSynchronize());++id;audit_activity_begin(id);uint64_t begin=stamp(CLOCK_MONOTONIC),cpu=stamp(CLOCK_PROCESS_CPUTIME_ID);
  operation();uint64_t returned=stamp(CLOCK_MONOTONIC);cu(cudaDeviceSynchronize());uint64_t end=stamp(CLOCK_MONOTONIC),cpu_end=stamp(CLOCK_PROCESS_CPUTIME_ID);audit_activity_end();
  std::cout<<"PHASE {\"id\":"<<id<<",\"name\":\""<<name<<"\",\"iteration\":"<<iteration<<",\"nrhs\":"<<nrhs<<",\"begin_ns\":"<<begin<<",\"end_ns\":"<<end<<",\"wall_seconds\":"<<(end-begin)*1e-9<<",\"api_seconds\":"<<(returned-begin)*1e-9<<",\"completion_sync_seconds\":"<<(end-returned)*1e-9<<",\"process_cpu_seconds\":"<<(cpu_end-cpu)*1e-9<<"}"<<std::endl;
  size_t free=0,total=0;cu(cudaMemGetInfo(&free,&total));std::cout<<"MEMORY {\"id\":"<<id<<",\"free\":"<<free<<",\"total\":"<<total<<"}"<<std::endl;
 };
 for(int iteration=0;iteration<=refactors;iteration++){
  int v=iteration==0?0:(iteration%2?1:2);
  if(single_values&&iteration)dval[0].upload(device_values[v].data(),nz*sizeof(T));
  void*matrix_values=dval[single_values?0:v].p;
  // Matrix descriptor changes and all vector preparation are outside timed calls.
#if BENCH_PANGU
  if(A)require(pangulu_matrix_destroy(A),"destroy matrix");A=nullptr;
  require(pangulu_matrix_create_sym(&A,csr_input?PANGULU_MATRIX_LAYOUT_CSR:PANGULU_MATRIX_LAYOUT_CSC,n,n,nz,1,dp.p,di.p,matrix_values,PANGULU_R_64I,PANGULU_R_32I,dtype,spd?PANGULU_MATRIX_SPD:PANGULU_MATRIX_GENERAL,PANGULU_MATRIX_VIEW_FULL,PANGULU_MEMORY_DEVICE),"GPU matrix");
  if(!iteration)phase("analysis",iteration,0,[&](){require(pangulu_execute(h,PANGULU_PHASE_ANALYSIS,A,nullptr,nullptr),"analysis");});
  phase(iteration?"refactor":"numeric",iteration,0,[&](){require(pangulu_execute(h,iteration?PANGULU_PHASE_REFACTORISATION:PANGULU_PHASE_NUMERIC_FACTORISATION,A,nullptr,nullptr),"factor");});
#else
  if(A)require(cudssMatrixDestroy(A),"destroy matrix");A=nullptr;
  require(cudssMatrixCreateCsr(&A,n,n,nz,dp.p,nullptr,di.p,matrix_values,CUDA_R_32I,dtype,spd?CUDSS_MTYPE_SPD:CUDSS_MTYPE_GENERAL,CUDSS_MVIEW_FULL,CUDSS_BASE_ZERO),"GPU matrix");
  if(!iteration){phase("reordering",iteration,0,[&](){require(cudssExecute(h,CUDSS_PHASE_REORDERING,config,data,A,nullptr,nullptr),"reorder");});phase("symbolic",iteration,0,[&](){require(cudssExecute(h,CUDSS_PHASE_SYMBOLIC_FACTORIZATION,config,data,A,nullptr,nullptr),"symbolic");});}
  phase(iteration?"refactor":"numeric",iteration,0,[&](){require(cudssExecute(h,iteration?CUDSS_PHASE_REFACTORIZATION:CUDSS_PHASE_FACTORIZATION,config,data,A,nullptr,nullptr),"factor");});
#endif
  std::vector<int>requests{-1,1};if(max_rhs>1)requests.push_back(max_rhs);
  for(int request:requests){
   bool physical=request==-1;if(physical&&(iteration||physical_rhs.empty()))continue;
   int nrhs=physical?1:request;void*rhs_pointer=physical?physical_b.p:db[v].p;
   cu(cudaMemset(dx.p,0,n*max_rhs*sizeof(T)));cu(cudaDeviceSynchronize());
#if BENCH_PANGU
   require(pangulu_multiple_vector_create(&B,n,nrhs,n,rhs_pointer,dtype,PANGULU_MEMORY_DEVICE),"b");require(pangulu_multiple_vector_create(&X,n,nrhs,n,dx.p,dtype,PANGULU_MEMORY_DEVICE),"x");
   phase(physical?"physical_solve":"solve",iteration,nrhs,[&](){require(pangulu_execute(h,PANGULU_PHASE_SOLVE,A,X,B),"solve");});
   require(pangulu_vector_destroy(B),"destroy b");require(pangulu_vector_destroy(X),"destroy x");B=X=nullptr;
#else
   require(cudssMatrixCreateDn(&B,n,nrhs,n,rhs_pointer,dtype,CUDSS_LAYOUT_COL_MAJOR),"b");require(cudssMatrixCreateDn(&X,n,nrhs,n,dx.p,dtype,CUDSS_LAYOUT_COL_MAJOR),"x");
   phase(physical?"physical_solve":"solve",iteration,nrhs,[&](){require(cudssExecute(h,CUDSS_PHASE_SOLVE,config,data,A,X,B),"solve");});
   require(cudssMatrixDestroy(B),"destroy b");require(cudssMatrixDestroy(X),"destroy x");B=X=nullptr;
#endif
   solutions.emplace_back(n*nrhs);cu(cudaMemcpy(solutions.back().data(),dx.p,n*nrhs*sizeof(T),cudaMemcpyDeviceToHost));solution_info.emplace_back(physical?-1:v,nrhs);
  }
 }
#if BENCH_PANGU
 require(pangulu_matrix_destroy(A),"destroy A");require(pangulu_destroy(h),"destroy handle");
#else
 require(cudssMatrixDestroy(A),"destroy A");require(cudssDataDestroy(h,data),"destroy data");require(cudssConfigDestroy(config),"destroy config");require(cudssDestroy(h),"destroy handle");
#endif
 bool passed=true;double worst=0;
 for(size_t s=0;s<solutions.size();s++){
  int v=solution_info[s].first,nrhs=solution_info[s].second;auto&x=solutions[s];
  for(auto z:x)if(!std::isfinite(std::abs(z)))throw std::runtime_error("nonfinite solution");
  if(v<0){
   auto residual=mv(cp,ri,original,x,1);for(size_t i=0;i<n;i++)residual[i]-=physical_rhs[i];
   double rr=std::sqrt(norm2(residual)/norm2(physical_rhs));bool good=std::isfinite(rr)&&rr<=1e-8;passed&=good;
   std::cout<<"CHECK {\"case\":"<<s<<",\"kind\":\"physical_rhs\",\"variant\":0,\"nrhs\":1,\"relative_residual\":"<<rr<<",\"relative_x_error\":0,\"known_solution\":false,\"passed\":"<<(good?"true":"false")<<"}"<<std::endl;
   const char*folder=std::getenv("PC_TRACE_DIR");if(!folder)throw std::runtime_error("missing output directory");
   std::ofstream output(std::string(folder)+"/physical_solution.bin",std::ios::binary);output.write((const char*)x.data(),x.size()*sizeof(T));if(!output)throw std::runtime_error("write solution failed");
   continue;
  }
  auto residual=mv(cp,ri,values[v],x,nrhs);std::vector<T>diff(x.size()),b(rhs[v].begin(),rhs[v].begin()+x.size());
  for(size_t i=0;i<x.size();i++){residual[i]-=b[i];diff[i]=x[i]-truth[i];}
  double rr=std::sqrt(norm2(residual)/norm2(b)),error=std::sqrt(norm2(diff)/norm2(std::vector<T>(truth.begin(),truth.begin()+x.size())));bool good=std::isfinite(rr)&&std::isfinite(error)&&rr<=1e-8&&error<=1e-6;passed&=good;worst=std::max(worst,error);
  std::cout<<"CHECK {\"case\":"<<s<<",\"variant\":"<<v<<",\"nrhs\":"<<nrhs<<",\"relative_residual\":"<<rr<<",\"relative_x_error\":"<<error<<",\"passed\":"<<(good?"true":"false")<<"}"<<std::endl;
 }
 std::cout<<"RESULT {\"passed\":"<<(passed?"true":"false")<<",\"checks\":"<<solutions.size()<<",\"maximum_relative_error\":"<<worst<<"}"<<std::endl;if(!passed)throw std::runtime_error("verification failed");
 dl_iterate_phdr(loaded,nullptr);
}
int main(int argc,char**argv){
 if(argc!=6){std::cerr<<"dataset real|complex general|spd refactors csc|csr\n";return 2;}
 bool active=false;
 try{std::cout<<std::setprecision(12);audit_activity_init();active=true;bool complex=std::string(argv[2])=="complex";
  if(complex)run<Z>(argv[1],std::string(argv[3])=="spd",std::stoi(argv[4]),std::string(argv[5])=="csr");else run<double>(argv[1],std::string(argv[3])=="spd",std::stoi(argv[4]),std::string(argv[5])=="csr");
  audit_activity_finish();return 0;
 }catch(const std::exception&e){std::cerr<<"DUEL_ERROR "<<e.what()<<std::endl;if(active)audit_activity_finish();return 1;}
}
