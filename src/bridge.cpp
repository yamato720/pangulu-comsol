#define _GNU_SOURCE
#include <cudss.h>
#include <cuda_runtime_api.h>
#include "protocol.h"
#include "profile.h"
#include "backend.h"
#include "device.h"
#include <dlfcn.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <spawn.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <limits.h>
#include <vector>
#include <string>
#include <map>
#include <algorithm>
#include <new>
#include <time.h>
extern char **environ;
static_assert(sizeof(cudaIpcMemHandle_t)==64,"CUDA IPC handle ABI differs from bridge protocol");
static bool direct_execution(){static bool direct=[](){const char *mode=getenv("PC_EXECUTION");return mode&&strcmp(mode,"direct")==0;}();return direct;}
struct LocalApi {
 void *library=nullptr;PanguBackend *(*create)(void)=nullptr;
 int (*dispatch)(PanguBackend*,const BridgeRequest*,BridgeResponse*,void*)=nullptr;
 void (*destroy)(PanguBackend*)=nullptr;
};
static LocalApi& local_api(){
 static LocalApi api=[](){
  LocalApi value;const char *path=getenv("PC_BACKEND");
  if(path)value.library=dlopen(path,RTLD_NOW|RTLD_LOCAL);
  if(value.library){
   value.create=(decltype(value.create))dlsym(value.library,"pc_backend_create");
   value.dispatch=(decltype(value.dispatch))dlsym(value.library,"pc_backend_dispatch");
   value.destroy=(decltype(value.destroy))dlsym(value.library,"pc_backend_destroy");
  }else fprintf(stderr,"PGL_DIRECT_LOAD_ERROR %s\n",path?dlerror():"PC_BACKEND is required");
  return value;
 }();return api;
}

struct cudssContext {int device=0;cudaStream_t stream=nullptr;};
struct cudssConfig {std::map<int,std::vector<unsigned char>> values;};
struct cudssMatrix {bool sparse=false;int64_t rows=0,cols=0,nnz=0,ld=0;void *starts=nullptr,*ends=nullptr,*indices=nullptr,*values=nullptr;cudaDataType_t indexType=CUDA_R_32I,valueType=CUDA_C_64F;cudssMatrixType_t type=CUDSS_MTYPE_GENERAL;cudssMatrixViewType_t view=CUDSS_MVIEW_FULL;cudssIndexBase_t base=CUDSS_BASE_ZERO;cudssLayout_t layout=CUDSS_LAYOUT_COL_MAJOR;};
struct cudssData {PanguBackend *local=nullptr;size_t memory_capacity=0;int socket=-1;pid_t worker=-1;int fd=-1;void *memory=MAP_FAILED;BridgeRequest request={};BridgeResponse last={};int info=0,index_bytes=4;bool analyzed=false,factored=false;const int *interrupt=nullptr;std::map<int,std::vector<unsigned char>> user;
 cudssMatrix_t staged_matrix=nullptr;int staged_device=-1;uint64_t epoch_counter=0;
 std::vector<uint64_t> scratch_ptr;std::vector<int32_t> scratch_idx;
 void *device_matrix=nullptr,*device_dense=nullptr;int *device_status=nullptr;
 size_t device_matrix_capacity=0,device_dense_capacity=0;
 cudaIpcMemHandle_t matrix_ipc={},dense_ipc={};bool device_valid=false;
 uint64_t value_counter=0;std::vector<void*> retired_matrix,retired_dense;
};
static void log_event(const char *event,const char *detail=""){char b[1024];int n=snprintf(b,sizeof(b),"PGL_BRIDGE {\"event\":\"%s\",\"pid\":%d,\"backend\":\"%s\",\"detail\":\"%s\"}\n",event,getpid(),direct_execution()?"pangulu_direct":"pangulu_worker",detail);if(n>0)write(STDERR_FILENO,b,std::min((size_t)n,sizeof(b)-1));}
#define bridge_now() PGL_NOW()
struct CopyMetrics {uint64_t requested_bytes=0,host_bytes=0,device_bytes=0;double seconds=0;};
static bool scalar_type(cudaDataType_t t){return t==CUDA_R_64F||t==CUDA_C_64F;}
static size_t item(cudaDataType_t t){return t==CUDA_C_64F?16:8;}
static cudssStatus_t get_bytes(const void*p,void*out,size_t n,CopyMetrics*metrics=nullptr){
 if(!p||!out)return CUDSS_STATUS_INVALID_VALUE;
 const double started=bridge_now();PGL_DETAIL(if(metrics)metrics->requested_bytes+=n;)
 cudaPointerAttributes a={};cudaError_t e=cudaPointerGetAttributes(&a,p);
 if(e==cudaSuccess&&(a.type==cudaMemoryTypeDevice||a.type==cudaMemoryTypeManaged)){
  cudssStatus_t result=cudaMemcpy(out,p,n,cudaMemcpyDeviceToHost)==cudaSuccess?CUDSS_STATUS_SUCCESS:CUDSS_STATUS_EXECUTION_FAILED;
  PGL_DETAIL(if(metrics){metrics->seconds+=bridge_now()-started;if(result==CUDSS_STATUS_SUCCESS)metrics->device_bytes+=n;})return result;
 }
 if(e!=cudaSuccess){cudaGetLastError();if(e!=cudaErrorInvalidValue){PGL_DETAIL(if(metrics)metrics->seconds+=bridge_now()-started;)return CUDSS_STATUS_EXECUTION_FAILED;}}
 memcpy(out,p,n);PGL_DETAIL(if(metrics){metrics->seconds+=bridge_now()-started;metrics->host_bytes+=n;})return CUDSS_STATUS_SUCCESS;
}
static cudssStatus_t put_bytes(void*p,const void*in,size_t n){
 if(!p||!in)return CUDSS_STATUS_INVALID_VALUE;
 cudaPointerAttributes a={};cudaError_t e=cudaPointerGetAttributes(&a,p);
 if(e==cudaSuccess&&(a.type==cudaMemoryTypeDevice||a.type==cudaMemoryTypeManaged))return cudaMemcpy(p,in,n,cudaMemcpyHostToDevice)==cudaSuccess?CUDSS_STATUS_SUCCESS:CUDSS_STATUS_EXECUTION_FAILED;
 if(e!=cudaSuccess){cudaGetLastError();if(e!=cudaErrorInvalidValue)return CUDSS_STATUS_EXECUTION_FAILED;}
 memcpy(p,in,n);return CUDSS_STATUS_SUCCESS;
}
static void unmap(cudssData_t d){if(d->memory!=MAP_FAILED){if(direct_execution())free(d->memory);else munmap(d->memory,d->request.total_bytes);}if(d->fd>=0)close(d->fd);d->memory=MAP_FAILED;d->fd=-1;d->memory_capacity=0;}
static void free_allocations(std::vector<void*>&allocations){for(void *p:allocations)cudaFree(p);allocations.clear();}
static void free_device(cudssData_t d){free_allocations(d->retired_matrix);free_allocations(d->retired_dense);if(d->device_matrix)cudaFree(d->device_matrix);if(d->device_dense)cudaFree(d->device_dense);if(d->device_status)cudaFree(d->device_status);}
static cudssStatus_t start_worker(cudssData_t d){
 if(d->socket>=0)return CUDSS_STATUS_SUCCESS;
 const char *worker=getenv("PC_WORKER");if(!worker||!*worker){log_event("error","PC_WORKER is required");return CUDSS_STATUS_NOT_INITIALIZED;}
 int sockets[2];if(socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0,sockets))return CUDSS_STATUS_ALLOC_FAILED;
 posix_spawn_file_actions_t actions;posix_spawn_file_actions_init(&actions);posix_spawn_file_actions_addclose(&actions,sockets[0]);posix_spawn_file_actions_adddup2(&actions,sockets[1],198);if(sockets[1]!=198)posix_spawn_file_actions_addclose(&actions,sockets[1]);
 std::vector<std::string> strings;for(char **e=environ;*e;e++)if(strncmp(*e,"LD_PRELOAD=",11)&&strncmp(*e,"LD_AUDIT=",9)&&strncmp(*e,"LD_LIBRARY_PATH=",16))strings.emplace_back(*e);
 const char*ld=getenv("PC_WORKER_LIBRARY_PATH");if(!ld){close(sockets[0]);close(sockets[1]);posix_spawn_file_actions_destroy(&actions);return CUDSS_STATUS_NOT_INITIALIZED;}strings.emplace_back(std::string("LD_LIBRARY_PATH=")+ld);
 std::vector<char*> env;for(auto&s:strings)env.push_back(const_cast<char*>(s.c_str()));env.push_back(nullptr);
 char argfd[]="198";char *argv[]={const_cast<char*>(worker),argfd,nullptr};
 int result=posix_spawn(&d->worker,worker,&actions,nullptr,argv,env.data());posix_spawn_file_actions_destroy(&actions);close(sockets[1]);if(result){close(sockets[0]);d->worker=-1;return CUDSS_STATUS_EXECUTION_FAILED;}
 d->socket=sockets[0];log_event("worker_started");return CUDSS_STATUS_SUCCESS;
}
static cudssStatus_t rpc(cudssData_t d,int command){
 const double started=bridge_now();
 if(d->interrupt&&*d->interrupt&&command!=BR_CLOSE){d->info=-4;return CUDSS_STATUS_EXECUTION_FAILED;}
 d->request.command=command;d->request.magic=PGL_BRIDGE_MAGIC;
 if(direct_execution()){
  auto &api=local_api();if(!api.create||!api.dispatch||!api.destroy)return CUDSS_STATUS_NOT_INITIALIZED;
  if(!d->local){d->local=api.create();if(!d->local)return CUDSS_STATUS_ALLOC_FAILED;log_event("direct_started","shared Pangu backend in COMSOL process");}
  api.dispatch(d->local,&d->request,&d->last,d->memory);
 }else{
  cudssStatus_t status=start_worker(d);if(status!=CUDSS_STATUS_SUCCESS)return status;
  if(bridge_send(d->socket,&d->request,sizeof(d->request),command==BR_CLOSE?-1:d->fd)){d->info=-1;return CUDSS_STATUS_EXECUTION_FAILED;}
  int extra=-1;if(bridge_receive(d->socket,&d->last,sizeof(d->last),&extra)){if(extra>=0)close(extra);d->info=-2;return CUDSS_STATUS_EXECUTION_FAILED;}if(extra>=0)close(extra);
 }
 if(d->last.magic!=PGL_BRIDGE_MAGIC){d->info=-3;return CUDSS_STATUS_EXECUTION_FAILED;}
 const double elapsed=bridge_now()-started;
 PGL_DETAIL(char timing[768];snprintf(timing,sizeof(timing),"command=%d structure_epoch=%llu roundtrip_seconds=%.9g worker_execute_seconds=%.9g conversion_seconds=%.9g value_update_seconds=%.9g pattern_seconds=%.9g release_seconds=%.9g canonical_nnz=%llu status=%d",command,(unsigned long long)d->request.structure_epoch,elapsed,d->last.analysis_seconds+d->last.factor_seconds+d->last.solve_seconds,d->last.conversion_seconds,d->last.value_update_seconds,d->last.pattern_seconds,d->last.release_seconds,(unsigned long long)d->last.canonical_nnz,d->last.status);log_event("rpc_timing",timing);)
 d->info=d->last.status?d->last.status:0;
 if(d->last.status){fprintf(stderr,"PGL_ERROR status=%d pangulu_status=%d cuda_status=%d command=%d\n",d->last.status,d->last.pangulu_status,d->last.cuda_status,command);return d->last.status==2?CUDSS_STATUS_ALLOC_FAILED:d->last.status==4?CUDSS_STATUS_NOT_SUPPORTED:CUDSS_STATUS_EXECUTION_FAILED;}
 if(command==BR_ANALYZE||command==BR_FACTOR)free_allocations(d->retired_matrix);
 if(command==BR_SOLVE)free_allocations(d->retired_dense);
 return CUDSS_STATUS_SUCCESS;
}
static bool diagnostic_compat(){const char*p=getenv("PC_COMSOL_DIAGNOSTICS");return p&&strcmp(p,"1")==0;}
static cudssStatus_t diagnostic_copy(void*out,size_t bytes,size_t*w,const void*source,size_t required){if(w)*w=required;if(!out&&bytes==0)return CUDSS_STATUS_SUCCESS;if(!out||bytes<required)return CUDSS_STATUS_INVALID_VALUE;return put_bytes(out,source,required);}
static cudssStatus_t validate_sparse(cudssMatrix_t A){
 if(!A||!A->sparse||A->rows!=A->cols||A->rows<=0||A->rows>INT_MAX||A->nnz<=0||A->nnz>INT_MAX||!scalar_type(A->valueType)||(A->type<CUDSS_MTYPE_GENERAL||A->type>CUDSS_MTYPE_HPD)||(A->view<CUDSS_MVIEW_FULL||A->view>CUDSS_MVIEW_UPPER)||(A->type==CUDSS_MTYPE_GENERAL&&A->view!=CUDSS_MVIEW_FULL)||!A->starts||!A->indices||!A->values)return CUDSS_STATUS_NOT_SUPPORTED;
 if(A->indexType!=CUDA_R_32I&&A->indexType!=CUDA_R_64I)return CUDSS_STATUS_NOT_SUPPORTED;
 if(A->base!=CUDSS_BASE_ZERO&&A->base!=CUDSS_BASE_ONE)return CUDSS_STATUS_INVALID_VALUE;
 const uint64_t budget=((uint64_t)A->rows+1)*8+(uint64_t)A->nnz*(4+item(A->valueType))+2*(uint64_t)A->rows*item(A->valueType)+63;
 if(budget>UINT64_C(20)*1024*1024*1024)return CUDSS_STATUS_ALLOC_FAILED;
 return CUDSS_STATUS_SUCCESS;
}
static cudssStatus_t read_pattern(cudssData_t d,cudssMatrix_t A,CopyMetrics&copies){
 const uint64_t n=(uint64_t)A->rows,nnz=(uint64_t)A->nnz;const int base=A->base==CUDSS_BASE_ONE?1:0;
 d->scratch_ptr.resize(n+1);d->scratch_idx.resize(nnz);auto&p=d->scratch_ptr;auto&i=d->scratch_idx;
 std::vector<int64_t> starts(n+1),ends;
 if(A->indexType==CUDA_R_64I){if(get_bytes(A->starts,starts.data(),(n+(A->ends?0:1))*8,&copies))return CUDSS_STATUS_EXECUTION_FAILED;if(A->ends){ends.resize(n);if(get_bytes(A->ends,ends.data(),n*8,&copies))return CUDSS_STATUS_EXECUTION_FAILED;starts[n]=ends.back();}}
 else {std::vector<int32_t> t(n+(A->ends?0:1));if(get_bytes(A->starts,t.data(),t.size()*4,&copies))return CUDSS_STATUS_EXECUTION_FAILED;for(size_t k=0;k<t.size();k++)starts[k]=t[k];if(A->ends){std::vector<int32_t> e(n);if(get_bytes(A->ends,e.data(),n*4,&copies))return CUDSS_STATUS_EXECUTION_FAILED;ends.assign(e.begin(),e.end());starts[n]=ends.back();}}
 for(uint64_t row=0;row<=n;row++){if(starts[row]<base||starts[row]>(int64_t)nnz+base)return CUDSS_STATUS_INVALID_VALUE;p[row]=(uint64_t)(starts[row]-base);if(row<n&&!ends.empty()&&ends[row]!=starts[row+1])return CUDSS_STATUS_NOT_SUPPORTED;if(row&&p[row]<p[row-1])return CUDSS_STATUS_INVALID_VALUE;}
 if(p[0]||p[n]!=nnz)return CUDSS_STATUS_INVALID_VALUE;
 if(A->indexType==CUDA_R_32I){if(get_bytes(A->indices,i.data(),nnz*4,&copies))return CUDSS_STATUS_EXECUTION_FAILED;for(uint64_t k=0;k<nnz;k++){if(i[k]<base||(uint64_t)i[k]>=(uint64_t)n+base)return CUDSS_STATUS_INVALID_VALUE;i[k]-=base;}}
 else {std::vector<int64_t> t(nnz);if(get_bytes(A->indices,t.data(),nnz*8,&copies))return CUDSS_STATUS_EXECUTION_FAILED;for(uint64_t k=0;k<nnz;k++){if(t[k]<base||t[k]>=(int64_t)n+base)return CUDSS_STATUS_INVALID_VALUE;i[k]=(int32_t)(t[k]-base);}}
 return CUDSS_STATUS_SUCCESS;
}
#if PGL_PROFILE
static void stage_log(cudssData_t d,const char*kind,double started,const CopyMetrics&copies,double pattern_seconds,double comparison_seconds,bool same){
 char msg[640];snprintf(msg,sizeof(msg),"kind=%s structure_epoch=%llu n=%llu nnz=%llu total_seconds=%.9g copy_seconds=%.9g pattern_read_seconds=%.9g pattern_compare_seconds=%.9g copy_requested_bytes=%llu host_copy_bytes=%llu device_to_host_bytes=%llu same_pattern=%d",kind,(unsigned long long)d->request.structure_epoch,(unsigned long long)d->request.n,(unsigned long long)d->request.nnz,bridge_now()-started,copies.seconds,pattern_seconds,comparison_seconds,(unsigned long long)copies.requested_bytes,(unsigned long long)copies.host_bytes,(unsigned long long)copies.device_bytes,same?1:0);log_event("matrix_stage_timing",msg);
}
#else
#define stage_log(...) ((void)0)
#endif
#if PGL_DEVICE
static bool device_source(const void *p,int device){
 if(!p)return false;cudaPointerAttributes a={};cudaError_t e=cudaPointerGetAttributes(&a,p);
 if(e!=cudaSuccess){cudaGetLastError();return false;}
 return a.type==cudaMemoryTypeManaged||(a.type==cudaMemoryTypeDevice&&a.device==device);
}
/* The exported allocations belong to this bridge, so IPC never depends on
 * the caller's allocator, suballocation offsets, or address in the worker. */
static cudssStatus_t allocate_device(void **p,size_t *capacity,size_t bytes,
 cudaIpcMemHandle_t *handle,std::vector<void*>&retired,bool &changed){
 changed=false;if(bytes<=*capacity)return CUDSS_STATUS_SUCCESS;
 void *next=nullptr;cudaError_t e=cudaMalloc(&next,bytes);if(e)return CUDSS_STATUS_ALLOC_FAILED;
 cudaIpcMemHandle_t exported={};
 if(!direct_execution()&&(e=cudaIpcGetMemHandle(&exported,next))!=cudaSuccess){cudaFree(next);cudaGetLastError();return CUDSS_STATUS_NOT_SUPPORTED;}
 if(*p)retired.push_back(*p);*p=next;*capacity=bytes;*handle=exported;changed=true;return CUDSS_STATUS_SUCCESS;
}
static cudssStatus_t stage_device_matrix(cudssData_t d,cudssHandle_t h,cudssMatrix_t A,bool &used,bool &same){
 used=false;same=false;
 if(!device_source(A->starts,h->device)||!device_source(A->indices,h->device)||
    !device_source(A->values,h->device)||(A->ends&&!device_source(A->ends,h->device)))return CUDSS_STATUS_SUCCESS;
 const double started=bridge_now();
 BridgeRequest q={};q.magic=PGL_BRIDGE_MAGIC;q.n=A->rows;q.nnz=A->nnz;q.nrhs=1;
 q.value_type=A->valueType;q.device=h->device;q.matrix_type=A->type;q.matrix_view=A->view;
 q.reuse=getenv("PC_REUSE")&&strcmp(getenv("PC_REUSE"),"refactor")==0;
 q.memory_space=direct_execution()?BR_MEMORY_DEVICE:BR_MEMORY_IPC;
 q.ptr_offset=0;q.idx_offset=(q.n+1)*4;
 q.val_offset=(q.idx_offset+q.nnz*4+63)&~UINT64_C(63);
 q.matrix_bytes=q.val_offset+q.nnz*item(A->valueType);
 bool allocated=false;cudssStatus_t status=allocate_device(&d->device_matrix,
  &d->device_matrix_capacity,q.matrix_bytes,&d->matrix_ipc,d->retired_matrix,allocated);
 if(status==CUDSS_STATUS_NOT_SUPPORTED){log_event("device_fallback","CUDA IPC export unavailable; using validated host transport");return CUDSS_STATUS_SUCCESS;}
 if(status)return status;
 if(!d->device_status&&cudaMalloc((void**)&d->device_status,sizeof(int))!=cudaSuccess)return CUDSS_STATUS_ALLOC_FAILED;
 bool compare=!allocated&&d->device_valid&&d->request.memory_space!=BR_MEMORY_HOST&&
  d->staged_device==h->device&&d->request.n==q.n&&d->request.nnz==q.nnz&&
  d->request.value_type==q.value_type&&d->request.matrix_type==q.matrix_type&&d->request.matrix_view==q.matrix_view;
 const double validation_started=bridge_now();
 int flags=0;int error=pgl_device_stage(A->starts,A->ends,A->indices,A->values,
  A->indexType==CUDA_R_64I?8:4,A->base==CUDSS_BASE_ONE?1:0,A->rows,A->nnz,
  (int)item(A->valueType),(int32_t*)d->device_matrix,
  (int32_t*)((char*)d->device_matrix+q.idx_offset),(char*)d->device_matrix+q.val_offset,
  compare,d->device_status,&flags);
 if(error||flags&(PGL_DEVICE_INVALID|PGL_DEVICE_NONCONTIGUOUS)){
  d->device_valid=false;d->analyzed=false;d->factored=false;used=true;
  return error?CUDSS_STATUS_EXECUTION_FAILED:flags&PGL_DEVICE_INVALID?CUDSS_STATUS_INVALID_VALUE:CUDSS_STATUS_NOT_SUPPORTED;
 }
 if(flags&PGL_DEVICE_UNSORTED){d->device_valid=false;log_event("device_fallback","unsorted CSR uses exact host canonicalization");return CUDSS_STATUS_SUCCESS;}
 same=compare&&!(flags&PGL_DEVICE_PATTERN_CHANGED);
 q.structure_epoch=same?d->request.structure_epoch:++d->epoch_counter;
 q.value_epoch=compare&&!(flags&PGL_DEVICE_VALUES_CHANGED)?d->request.value_epoch:++d->value_counter;
 if(direct_execution())q.matrix_address=(uint64_t)(uintptr_t)d->device_matrix;
 else memcpy(q.matrix_ipc,&d->matrix_ipc,sizeof(d->matrix_ipc));
 unmap(d);d->request=q;d->device_valid=true;d->staged_matrix=A;d->staged_device=h->device;
 d->index_bytes=A->indexType==CUDA_R_64I?8:4;d->factored=false;if(!same)d->analyzed=false;
 used=true;CopyMetrics copies;
 PGL_DETAIL(copies.requested_bytes=sizeof(int);copies.device_bytes=sizeof(int);)
 stage_log(d,same?"device_exact_refresh":"device_full",started,copies,bridge_now()-validation_started,0,same);
 return CUDSS_STATUS_SUCCESS;
}
static cudssStatus_t solve_device(cudssData_t d,cudssMatrix_t A,cudssMatrix_t x,cudssMatrix_t b){
 uint64_t bytes=(uint64_t)A->rows*(uint64_t)b->cols*item(A->valueType);
 if(bytes>UINT64_C(10)*1024*1024*1024)return CUDSS_STATUS_ALLOC_FAILED;
 bool allocated=false;cudssStatus_t status=allocate_device(&d->device_dense,
  &d->device_dense_capacity,(size_t)(2*bytes),&d->dense_ipc,d->retired_dense,allocated);
 if(status)return status;
 d->request.nrhs=b->cols;d->request.b_offset=0;d->request.x_offset=bytes;d->request.dense_bytes=2*bytes;
 if(direct_execution())d->request.dense_address=(uint64_t)(uintptr_t)d->device_dense;
 else memcpy(d->request.dense_ipc,&d->dense_ipc,sizeof(d->dense_ipc));
 if(cudaMemcpy(d->device_dense,b->values,(size_t)bytes,cudaMemcpyDefault)!=cudaSuccess)return CUDSS_STATUS_EXECUTION_FAILED;
 status=rpc(d,BR_SOLVE);if(status)return status;
 void *solution=(char*)d->device_dense+bytes;int invalid=0;
 if(pgl_device_finite(solution,(size_t)bytes/8,d->device_status,&invalid)||invalid){d->info=5;return CUDSS_STATUS_EXECUTION_FAILED;}
 return cudaMemcpy(x->values,solution,(size_t)bytes,cudaMemcpyDefault)==cudaSuccess?CUDSS_STATUS_SUCCESS:CUDSS_STATUS_EXECUTION_FAILED;
}
#endif
// scratch_ptr/idx contain a fully validated, normalized readback of the actual
// caller CSR. A fresh epoch is assigned only when this complete snapshot changes.
static cudssStatus_t commit_full_stage(cudssData_t d,cudssHandle_t h,cudssMatrix_t A,CopyMetrics&copies){
 d->device_valid=false;
 d->analyzed=false;d->factored=false;if(!direct_execution())unmap(d);
 BridgeRequest q={};q.magic=PGL_BRIDGE_MAGIC;q.n=A->rows;q.nnz=A->nnz;q.nrhs=1;q.value_type=A->valueType;q.device=h->device;q.reuse=getenv("PC_REUSE")&&strcmp(getenv("PC_REUSE"),"refactor")==0;
 q.structure_epoch=++d->epoch_counter;q.matrix_type=(int)A->type;q.matrix_view=(int)A->view;
 q.ptr_offset=0;q.idx_offset=(q.n+1)*8;q.val_offset=(q.idx_offset+q.nnz*4+63)&~UINT64_C(63);q.b_offset=q.val_offset+q.nnz*item(A->valueType);q.x_offset=q.b_offset+q.n*item(A->valueType);q.total_bytes=q.x_offset+q.n*item(A->valueType);
 if(q.total_bytes>UINT64_C(20)*1024*1024*1024)return CUDSS_STATUS_ALLOC_FAILED;
 d->request=q;d->index_bytes=A->indexType==CUDA_R_64I?8:4;
 if(direct_execution()){
  if(q.total_bytes>d->memory_capacity){void *next=realloc(d->memory==MAP_FAILED?nullptr:d->memory,q.total_bytes);if(!next)return CUDSS_STATUS_ALLOC_FAILED;d->memory=next;d->memory_capacity=q.total_bytes;}
 }else{
  d->fd=memfd_create("comsol_pangulu_csr",MFD_CLOEXEC);if(d->fd<0||ftruncate(d->fd,q.total_bytes)){unmap(d);return CUDSS_STATUS_ALLOC_FAILED;}
  d->memory=mmap(nullptr,q.total_bytes,PROT_READ|PROT_WRITE,MAP_SHARED,d->fd,0);if(d->memory==MAP_FAILED){unmap(d);return CUDSS_STATUS_ALLOC_FAILED;}
 }
 const double host_started=bridge_now();memcpy((char*)d->memory+q.ptr_offset,d->scratch_ptr.data(),(q.n+1)*8);memcpy((char*)d->memory+q.idx_offset,d->scratch_idx.data(),q.nnz*4);PGL_DETAIL(copies.host_bytes+=(q.n+1)*8+q.nnz*4;copies.requested_bytes+=(q.n+1)*8+q.nnz*4;copies.seconds+=bridge_now()-host_started;)
 cudssStatus_t status=get_bytes(A->values,(char*)d->memory+q.val_offset,q.nnz*item(A->valueType),&copies);
 if(status==CUDSS_STATUS_SUCCESS){d->staged_matrix=A;d->staged_device=h->device;}return status;
}
static cudssStatus_t stage_matrix(cudssData_t d,cudssHandle_t h,cudssMatrix_t A){
 const double started=bridge_now();cudssStatus_t status=validate_sparse(A);if(status)return status;CopyMetrics copies;
#if PGL_DEVICE
 bool used=false,same=false;status=stage_device_matrix(d,h,A,used,same);if(status||used)return status;
#endif
 const double pattern_started=bridge_now();status=read_pattern(d,A,copies);const double pattern_seconds=bridge_now()-pattern_started;if(status)return status;
 status=commit_full_stage(d,h,A,copies);stage_log(d,"full",started,copies,pattern_seconds,0,false);return status;
}
static cudssStatus_t refresh_factor_matrix(cudssData_t d,cudssHandle_t h,cudssMatrix_t A,bool&same){
 const double started=bridge_now();cudssStatus_t status=validate_sparse(A);if(status)return status;CopyMetrics copies;
#if PGL_DEVICE
 bool used=false;status=stage_device_matrix(d,h,A,used,same);if(status||used)return status;
#endif
 const double pattern_started=bridge_now();status=read_pattern(d,A,copies);const double pattern_seconds=bridge_now()-pattern_started;if(status)return status;
 const double comparison_started=bridge_now();
 same=d->memory!=MAP_FAILED&&d->staged_matrix==A&&d->staged_device==h->device&&d->request.n==(uint64_t)A->rows&&d->request.nnz==(uint64_t)A->nnz&&d->request.value_type==(uint64_t)A->valueType&&d->request.matrix_type==(int)A->type&&d->request.matrix_view==(int)A->view
   &&memcmp((char*)d->memory+d->request.ptr_offset,d->scratch_ptr.data(),((size_t)A->rows+1)*8)==0
   &&memcmp((char*)d->memory+d->request.idx_offset,d->scratch_idx.data(),(size_t)A->nnz*4)==0;
 const double comparison_seconds=bridge_now()-comparison_started;
 if(same){
  d->factored=false;d->index_bytes=A->indexType==CUDA_R_64I?8:4;
  status=get_bytes(A->values,(char*)d->memory+d->request.val_offset,(size_t)A->nnz*item(A->valueType),&copies);
 }else status=commit_full_stage(d,h,A,copies);
 stage_log(d,same?"factor_values_refresh":"factor_pattern_changed",started,copies,pattern_seconds,comparison_seconds,same);return status;
}
#if PGL_PROFILE
struct ExecuteSpan {
 cudssData_t data;int phase;uint64_t begin;
 ExecuteSpan(cudssData_t d,int p):data(d),phase(p),begin(pgl_profile_ns()){}
 ~ExecuteSpan(){uint64_t end=pgl_profile_ns();fprintf(stderr,"PGL_EXEC_SPAN {\"phase\":%d,\"epoch\":%llu,\"begin_ns\":%llu,\"end_ns\":%llu}\n",phase,(unsigned long long)(data?data->request.structure_epoch:0),(unsigned long long)begin,(unsigned long long)end);}
};
#define PGL_EXEC_SCOPE(d,p) ExecuteSpan pgl_execute_span(d,p)
#else
#define PGL_EXEC_SCOPE(d,p) ((void)0)
#endif
extern "C" {
cudssStatus_t cudssCreate(cudssHandle_t*h){if(!h)return CUDSS_STATUS_INVALID_VALUE;*h=new(std::nothrow)cudssContext;if(!*h)return CUDSS_STATUS_ALLOC_FAILED;if(cudaGetDevice(&(*h)->device)!=cudaSuccess){delete *h;*h=nullptr;return CUDSS_STATUS_NOT_INITIALIZED;}log_event("create","cuDSS ABI 0.7.1 mapped to Pangu, no native cuDSS fallback");log_event("execution_policy","version=v6_gpu_resident online_residual_check=off iterative_refinement=off diagnostic_matrix_hashes=off automatic_factor_retry=off");log_event("device_transport",PGL_DEVICE?"GPU CSR exact validation and CUDA IPC enabled":"HOST fallback; build with PC_CUDA_COMPILER to enable GPU CSR");return CUDSS_STATUS_SUCCESS;}
cudssStatus_t cudssCreateMg(cudssHandle_t*h,int count,int*devices){if(count!=1||!devices)return CUDSS_STATUS_NOT_SUPPORTED;if(cudaSetDevice(devices[0])!=cudaSuccess)return CUDSS_STATUS_INVALID_VALUE;return cudssCreate(h);}
cudssStatus_t cudssDestroy(cudssHandle_t h){if(!h)return CUDSS_STATUS_INVALID_VALUE;delete h;return CUDSS_STATUS_SUCCESS;}
cudssStatus_t cudssSetStream(cudssHandle_t h,cudaStream_t stream){if(!h)return CUDSS_STATUS_INVALID_VALUE;h->stream=stream;return CUDSS_STATUS_SUCCESS;}
cudssStatus_t cudssGetProperty(libraryPropertyType type,int*value){if(!value)return CUDSS_STATUS_INVALID_VALUE;switch(type){case MAJOR_VERSION:*value=0;break;case MINOR_VERSION:*value=7;break;case PATCH_LEVEL:*value=1;break;default:return CUDSS_STATUS_INVALID_VALUE;}return CUDSS_STATUS_SUCCESS;}
cudssStatus_t cudssConfigCreate(cudssConfig_t*c){if(!c)return CUDSS_STATUS_INVALID_VALUE;*c=new(std::nothrow)cudssConfig;return *c?CUDSS_STATUS_SUCCESS:CUDSS_STATUS_ALLOC_FAILED;}
cudssStatus_t cudssConfigDestroy(cudssConfig_t c){if(!c)return CUDSS_STATUS_INVALID_VALUE;delete c;return CUDSS_STATUS_SUCCESS;}
cudssStatus_t cudssConfigSet(cudssConfig_t c,cudssConfigParam_t p,void*v,size_t n){if(!c||!v||!n||n>4096)return CUDSS_STATUS_INVALID_VALUE;if(p==CUDSS_CONFIG_SOLVE_MODE&&n>=4&&*(int*)v!=0)return CUDSS_STATUS_NOT_SUPPORTED;c->values[(int)p]=std::vector<unsigned char>((unsigned char*)v,(unsigned char*)v+n);PGL_DETAIL(char line[128];snprintf(line,sizeof(line),"param=%d bytes=%zu; numerical policy delegated to Pangu",(int)p,n);log_event("config",line);)return CUDSS_STATUS_SUCCESS;}
cudssStatus_t cudssConfigGet(cudssConfig_t c,cudssConfigParam_t p,void*v,size_t n,size_t*w){if(!c)return CUDSS_STATUS_INVALID_VALUE;auto it=c->values.find((int)p);if(it==c->values.end())return CUDSS_STATUS_NOT_SUPPORTED;if(w)*w=it->second.size();if(!v&&n==0)return CUDSS_STATUS_SUCCESS;if(n<it->second.size()||!v)return CUDSS_STATUS_INVALID_VALUE;memcpy(v,it->second.data(),it->second.size());return CUDSS_STATUS_SUCCESS;}
cudssStatus_t cudssDataCreate(cudssHandle_t h,cudssData_t*d){if(!h||!d)return CUDSS_STATUS_INVALID_VALUE;*d=new(std::nothrow)cudssData;return *d?CUDSS_STATUS_SUCCESS:CUDSS_STATUS_ALLOC_FAILED;}
cudssStatus_t cudssDataDestroy(cudssHandle_t h,cudssData_t d){if(!d)return CUDSS_STATUS_INVALID_VALUE;if(h)cudaSetDevice(h->device);if(d->local){local_api().destroy(d->local);d->local=nullptr;}if(d->socket>=0){rpc(d,BR_CLOSE);close(d->socket);d->socket=-1;}if(d->worker>0){int status;waitpid(d->worker,&status,0);}unmap(d);free_device(d);delete d;return CUDSS_STATUS_SUCCESS;}
cudssStatus_t cudssDataSet(cudssHandle_t h,cudssData_t d,cudssDataParam_t p,void*v,size_t n){if(!h||!d||!v||n==0)return CUDSS_STATUS_INVALID_VALUE;if(p==CUDSS_DATA_INFO){if(n!=4)return CUDSS_STATUS_INVALID_VALUE;memcpy(&d->info,v,4);return CUDSS_STATUS_SUCCESS;}if(p==CUDSS_DATA_USER_HOST_INTERRUPT){if(n!=4)return CUDSS_STATUS_INVALID_VALUE;d->interrupt=(const int*)v;log_event("host_interrupt_registered","checked between Pangu phases; in-phase asynchronous cancellation not implemented");return CUDSS_STATUS_SUCCESS;}log_event("unsupported_data_set");return CUDSS_STATUS_NOT_SUPPORTED;}
cudssStatus_t cudssDataGet(cudssHandle_t h,cudssData_t d,cudssDataParam_t p,void*v,size_t n,size_t*w){
 if(!h||!d)return CUDSS_STATUS_INVALID_VALUE;
 if(p==CUDSS_DATA_INFO)return diagnostic_copy(v,n,w,&d->info,4);
 if(diagnostic_compat()&&p==CUDSS_DATA_NPIVOTS){log_event("diagnostic_semantics_not_equivalent","NPIVOTS=-1 means unavailable; Pangu public API does not expose perturbed-pivot counts");int64_t unknown=-1;return diagnostic_copy(v,n,w,&unknown,d->index_bytes);}
 if(diagnostic_compat()&&p==CUDSS_DATA_MEMORY_ESTIMATES){
  // Pangu's opaque ANALYSIS has already allocated its factorable state. These
  // are explicitly a remaining-buffer compatibility budget, not cuDSS LU
  // memory estimates; all unobservable components are left unspecified (zero).
  int64_t estimates[16]={};estimates[1]=(int64_t)(2*d->request.n*(d->request.value_type==5?16:8));
  log_event("diagnostic_semantics_not_equivalent","MEMORY_ESTIMATES compatibility reports only known remaining RHS/X buffers; native cuDSS memory-estimate ABI is not reproduced");
  return diagnostic_copy(v,n,w,estimates,sizeof(estimates));
 }
 char msg[96];snprintf(msg,sizeof(msg),"param=%d bytes=%zu",(int)p,n);log_event("unsupported_data_get",msg);return CUDSS_STATUS_NOT_SUPPORTED;
}
cudssStatus_t cudssMatrixCreateCsr(cudssMatrix_t*m,int64_t rows,int64_t cols,int64_t nnz,void*starts,void*ends,void*indices,void*values,cudaDataType_t indexType,cudaDataType_t valueType,cudssMatrixType_t type,cudssMatrixViewType_t view,cudssIndexBase_t base){if(!m||rows<=0||cols<=0||nnz<=0||!starts||!indices)return CUDSS_STATUS_INVALID_VALUE;*m=new(std::nothrow)cudssMatrix;if(!*m)return CUDSS_STATUS_ALLOC_FAILED;(*m)->sparse=true;(*m)->rows=rows;(*m)->cols=cols;(*m)->nnz=nnz;(*m)->starts=starts;(*m)->ends=ends;(*m)->indices=indices;(*m)->values=values;(*m)->indexType=indexType;(*m)->valueType=valueType;(*m)->type=type;(*m)->view=view;(*m)->base=base;return CUDSS_STATUS_SUCCESS;}
cudssStatus_t cudssMatrixCreateDn(cudssMatrix_t*m,int64_t rows,int64_t cols,int64_t ld,void*values,cudaDataType_t type,cudssLayout_t layout){if(!m||rows<=0||cols<=0||ld<=0)return CUDSS_STATUS_INVALID_VALUE;*m=new(std::nothrow)cudssMatrix;if(!*m)return CUDSS_STATUS_ALLOC_FAILED;(*m)->rows=rows;(*m)->cols=cols;(*m)->ld=ld;(*m)->values=values;(*m)->valueType=type;(*m)->layout=layout;return CUDSS_STATUS_SUCCESS;}
cudssStatus_t cudssMatrixDestroy(cudssMatrix_t m){if(!m)return CUDSS_STATUS_INVALID_VALUE;delete m;return CUDSS_STATUS_SUCCESS;}
cudssStatus_t cudssExecute(cudssHandle_t h,int phase,cudssConfig_t c,cudssData_t d,cudssMatrix_t A,cudssMatrix_t x,cudssMatrix_t b){
 PGL_EXEC_SCOPE(d,phase);
 if(!h||!c||!d||!A)return CUDSS_STATUS_INVALID_VALUE;if(cudaSetDevice(h->device)!=cudaSuccess)return CUDSS_STATUS_EXECUTION_FAILED;if(cudaDeviceSynchronize()!=cudaSuccess)return CUDSS_STATUS_EXECUTION_FAILED;
 int allowed=CUDSS_PHASE_ANALYSIS|CUDSS_PHASE_FACTORIZATION|CUDSS_PHASE_REFACTORIZATION|CUDSS_PHASE_SOLVE;if(phase<=0||(phase&~allowed))return CUDSS_STATUS_NOT_SUPPORTED;
 cudssStatus_t status;
 if((phase&CUDSS_PHASE_REORDERING)&&!(phase&CUDSS_PHASE_SYMBOLIC_FACTORIZATION)){
  d->analyzed=false;d->factored=false;log_event("analysis_deferred","opaque Pangu analysis is performed once at the symbolic phase");
 }
 if(phase&CUDSS_PHASE_SYMBOLIC_FACTORIZATION){status=stage_matrix(d,h,A);if(status)return status;status=rpc(d,BR_ANALYZE);if(status)return status;d->analyzed=true;d->factored=false;}
 if(phase&(CUDSS_PHASE_FACTORIZATION|CUDSS_PHASE_REFACTORIZATION)){
  bool same=false;status=refresh_factor_matrix(d,h,A,same);if(status)return status;
  if(!d->analyzed){status=rpc(d,BR_ANALYZE);if(status)return status;d->analyzed=true;}
  status=rpc(d,BR_FACTOR);if(status)return status;d->analyzed=true;d->factored=true;
 }
 if(phase&CUDSS_PHASE_SOLVE){
  if((phase&CUDSS_PHASE_SOLVE)!=CUDSS_PHASE_SOLVE||!x||!b||x->sparse||b->sparse||x->rows!=A->rows||b->rows!=A->rows||x->cols<=0||x->cols>65536||b->cols!=x->cols||!((x->layout==CUDSS_LAYOUT_COL_MAJOR&&x->ld>=x->rows&&(x->cols==1||x->ld==x->rows))||(x->layout==CUDSS_LAYOUT_ROW_MAJOR&&x->ld==1&&x->cols==1))||!((b->layout==CUDSS_LAYOUT_COL_MAJOR&&b->ld>=b->rows&&(b->cols==1||b->ld==b->rows))||(b->layout==CUDSS_LAYOUT_ROW_MAJOR&&b->ld==1&&b->cols==1))||x->valueType!=A->valueType||b->valueType!=A->valueType)return CUDSS_STATUS_NOT_SUPPORTED;
  if(!d->factored)return CUDSS_STATUS_NOT_INITIALIZED;
#if PGL_DEVICE
  if(d->request.memory_space!=BR_MEMORY_HOST)return solve_device(d,A,x,b);
#endif
  if(d->memory==MAP_FAILED)return CUDSS_STATUS_NOT_INITIALIZED;
  uint64_t dense=(uint64_t)A->rows*(uint64_t)b->cols*item(A->valueType);
  uint64_t total=d->request.b_offset+2*dense;
  if(total>UINT64_C(20)*1024*1024*1024)return CUDSS_STATUS_ALLOC_FAILED;
  if(d->request.nrhs!=(uint64_t)b->cols){
   void *mapping;
   if(direct_execution()){
    mapping=d->memory;if(total>d->memory_capacity){mapping=realloc(d->memory,total);if(!mapping)return CUDSS_STATUS_ALLOC_FAILED;d->memory_capacity=total;}
   }else{
    if(ftruncate(d->fd,total))return CUDSS_STATUS_ALLOC_FAILED;
    mapping=mremap(d->memory,d->request.total_bytes,total,MREMAP_MAYMOVE);if(mapping==MAP_FAILED)return CUDSS_STATUS_ALLOC_FAILED;
   }
   d->memory=mapping;d->request.nrhs=b->cols;d->request.x_offset=d->request.b_offset+dense;d->request.total_bytes=total;
  }
  status=get_bytes(b->values,(char*)d->memory+d->request.b_offset,(size_t)dense);if(status)return status;
  status=rpc(d,BR_SOLVE);if(status)return status;
  return put_bytes(x->values,(char*)d->memory+d->request.x_offset,(size_t)dense);
 }
 return CUDSS_STATUS_SUCCESS;
}
}
