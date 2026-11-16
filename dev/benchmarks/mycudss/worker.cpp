#include "protocol.h"
#include "profile.h"
#include <cudss.h>
#include <cuda_runtime_api.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <map>
#include <algorithm>
#include <cmath>

struct NativeError {cudssStatus_t status;cudaError_t cuda;};
static void ok(cudssStatus_t s){if(s)throw NativeError{s,cudaSuccess};}
static void cu(cudaError_t e){if(e)throw NativeError{CUDSS_STATUS_EXECUTION_FAILED,e};}
struct Buffer {
 void *p=nullptr;size_t capacity=0;
 void reserve(size_t size){if(size>capacity){if(p)cu(cudaFree(p));p=nullptr;capacity=0;cu(cudaMalloc(&p,size));capacity=size;}}
 ~Buffer(){if(p)cudaFree(p);}
};
struct State {
 cudssHandle_t h=nullptr;cudssConfig_t config=nullptr;cudssData_t data=nullptr;cudssMatrix_t A=nullptr,B=nullptr,X=nullptr;
 Buffer ptr,idx,val,b,x;uint64_t epoch=0,n=0,nnz=0;int value_type=0,index_bytes=0,interrupt=0;
 std::map<int,std::vector<unsigned char>> settings;
 ~State(){if(X)cudssMatrixDestroy(X);if(B)cudssMatrixDestroy(B);if(A)cudssMatrixDestroy(A);if(data)cudssDataDestroy(h,data);if(config)cudssConfigDestroy(config);if(h)cudssDestroy(h);}
 void init(int device){if(h)return;cu(cudaSetDevice(device));ok(cudssCreate(&h));ok(cudssConfigCreate(&config));ok(cudssDataCreate(h,&data));ok(cudssDataSet(h,data,CUDSS_DATA_USER_HOST_INTERRUPT,&interrupt,sizeof(interrupt)));}
 void configure(const BridgeRequest&q){
  for(uint32_t k=0;k<q.config_count;k++){
   auto &c=q.config[k];std::vector<unsigned char> bytes(c.value,c.value+c.bytes);
   auto found=settings.find(c.param);if(found!=settings.end()&&found->second==bytes)continue;
   ok(cudssConfigSet(config,(cudssConfigParam_t)c.param,bytes.data(),bytes.size()));settings[c.param]=std::move(bytes);
  }
 }
 void matrix(const BridgeRequest&q,const char*memory){
  size_t item=q.value_type==CUDA_C_64F?16:8;
  if(epoch!=q.structure_epoch){
   if(A){ok(cudssMatrixDestroy(A));A=nullptr;}
   if(B){ok(cudssMatrixDestroy(B));B=nullptr;}if(X){ok(cudssMatrixDestroy(X));X=nullptr;}
   ptr.reserve((q.n+1)*q.index_bytes);idx.reserve(q.nnz*q.index_bytes);val.reserve(q.nnz*item);b.reserve(q.n*item);x.reserve(q.n*item);
   const uint64_t *rp=(const uint64_t*)(memory+q.ptr_offset);const int32_t *ci=(const int32_t*)(memory+q.idx_offset);
   if(q.index_bytes==4){std::vector<int32_t> tmp(q.n+1);for(uint64_t i=0;i<=q.n;i++)tmp[i]=(int32_t)rp[i];cu(cudaMemcpy(ptr.p,tmp.data(),tmp.size()*4,cudaMemcpyHostToDevice));cu(cudaMemcpy(idx.p,ci,q.nnz*4,cudaMemcpyHostToDevice));}
   else {std::vector<int64_t> tmp(q.nnz);for(uint64_t i=0;i<q.nnz;i++)tmp[i]=ci[i];cu(cudaMemcpy(ptr.p,rp,(q.n+1)*8,cudaMemcpyHostToDevice));cu(cudaMemcpy(idx.p,tmp.data(),tmp.size()*8,cudaMemcpyHostToDevice));}
   ok(cudssMatrixCreateCsr(&A,q.n,q.n,q.nnz,ptr.p,nullptr,idx.p,val.p,q.index_bytes==4?CUDA_R_32I:CUDA_R_64I,(cudaDataType_t)q.value_type,CUDSS_MTYPE_GENERAL,CUDSS_MVIEW_FULL,CUDSS_BASE_ZERO));
   ok(cudssMatrixCreateDn(&B,q.n,1,q.n,b.p,(cudaDataType_t)q.value_type,CUDSS_LAYOUT_COL_MAJOR));ok(cudssMatrixCreateDn(&X,q.n,1,q.n,x.p,(cudaDataType_t)q.value_type,CUDSS_LAYOUT_COL_MAJOR));
   epoch=q.structure_epoch;n=q.n;nnz=q.nnz;value_type=q.value_type;index_bytes=q.index_bytes;
  }
  if(q.phase&CUDSS_PHASE_REORDERING || q.command==BR_FACTOR)cu(cudaMemcpy(val.p,memory+q.val_offset,q.nnz*item,cudaMemcpyHostToDevice));
 }
};

int main(int argc,char**argv){
 if(argc!=2)return 2;int socket=atoi(argv[1]);State state;
 for(;;){
  BridgeRequest q={};BridgeResponse r={};r.magic=PGL_BRIDGE_MAGIC;int fd=-1;void *mapping=MAP_FAILED;
  if(bridge_receive(socket,&q,sizeof(q),&fd))break;
  if(q.magic!=PGL_BRIDGE_MAGIC){if(fd>=0)close(fd);break;}
  if(q.command==BR_CLOSE){bridge_send(socket,&r,sizeof(r),-1);break;}
  r.n=q.n;r.nnz=q.nnz;r.canonical_nnz=q.nnz;
  try{
   state.init(q.device);state.interrupt=q.host_interrupt;
   if(q.config_count>32)throw NativeError{CUDSS_STATUS_INVALID_VALUE,cudaSuccess};
   state.configure(q);
   if(q.command==BR_DATAGET){
    size_t written=0;if(q.bytes>sizeof(r.payload))throw NativeError{CUDSS_STATUS_NOT_SUPPORTED,cudaSuccess};
    ok(cudssDataGet(state.h,state.data,(cudssDataParam_t)q.param,q.bytes?r.payload:nullptr,q.bytes,&written));r.bytes=written;
   }else if(q.command==BR_DATASET){
    if(q.bytes>sizeof(q.payload))throw NativeError{CUDSS_STATUS_NOT_SUPPORTED,cudaSuccess};
    ok(cudssDataSet(state.h,state.data,(cudssDataParam_t)q.param,q.payload,q.bytes));
   }else{
    struct stat st;if(fd<0||fstat(fd,&st)||q.total_bytes>(uint64_t)st.st_size||!q.n||!q.nnz||(q.index_bytes!=4&&q.index_bytes!=8))throw NativeError{CUDSS_STATUS_INVALID_VALUE,cudaSuccess};
    mapping=mmap(nullptr,q.total_bytes,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);if(mapping==MAP_FAILED)throw NativeError{CUDSS_STATUS_ALLOC_FAILED,cudaSuccess};
    char *memory=(char*)mapping;size_t item=q.value_type==CUDA_C_64F?16:8;
    double load_start=PGL_NOW();
    if(q.command==BR_ANALYZE||q.command==BR_FACTOR)state.matrix(q,memory);
    if(q.command==BR_SOLVE)cu(cudaMemcpy(state.b.p,memory+q.b_offset,q.n*item,cudaMemcpyHostToDevice));
    PGL_TIME_ADD(r.value_update_seconds,load_start);
    PGL_DETAIL(uint64_t begin=pgl_profile_ns();)
    ok(cudssExecute(state.h,q.phase,state.config,state.data,state.A,state.X,state.B));
    PGL_DETAIL(uint64_t returned=pgl_profile_ns();)
    cu(cudaDeviceSynchronize());
#if PGL_PROFILE
    uint64_t end=pgl_profile_ns();double seconds=(end-begin)*1e-9;
    if(q.command==BR_ANALYZE)r.analysis_seconds=seconds;else if(q.command==BR_FACTOR)r.factor_seconds=seconds;else r.solve_seconds=seconds;
    fprintf(stderr,"MYCUDSS_PHASE {\"epoch\":%llu,\"phase\":%d,\"api_seconds\":%.9g,\"completion_sync_seconds\":%.9g,\"seconds\":%.9g,\"load_seconds\":%.9g}\n",(unsigned long long)q.structure_epoch,q.phase,(returned-begin)*1e-9,(end-returned)*1e-9,seconds,r.value_update_seconds);
#endif
    if(q.command==BR_SOLVE){cu(cudaMemcpy(memory+q.x_offset,state.x.p,q.n*item,cudaMemcpyDeviceToHost));const double *values=(const double*)(memory+q.x_offset);for(size_t i=0;i<q.n*item/8;i++)if(!std::isfinite(values[i]))throw NativeError{CUDSS_STATUS_EXECUTION_FAILED,cudaSuccess};}
   }
  }catch(const NativeError&e){r.status=e.status;r.cuda_status=e.cuda;}catch(const std::bad_alloc&){r.status=CUDSS_STATUS_ALLOC_FAILED;}
  if(mapping!=MAP_FAILED)munmap(mapping,q.total_bytes);if(fd>=0)close(fd);
  if(bridge_send(socket,&r,sizeof(r),-1))break;
 }
 close(socket);return 0;
}
