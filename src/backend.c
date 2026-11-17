#define _GNU_SOURCE
#include "backend.h"
#include "profile.h"
#include "pattern_cache.h"
#include <pangulu.h>
#include <cuda_runtime_api.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <stdlib.h>
#include <stdio.h>
#include <time.h>
#include <math.h>
#include <errno.h>
#include <limits.h>
#include <stdarg.h>
#include <fcntl.h>

extern int pangulu_license_status(char *message, size_t capacity);
_Static_assert(sizeof(cudaIpcMemHandle_t)==64,"CUDA IPC handle ABI differs from bridge protocol");

typedef struct {double r,i;} Z;
#define now() PGL_NOW()
#if PGL_PROFILE
static void write_log(const char *p,size_t bytes){ssize_t ignored=write(STDERR_FILENO,p,bytes);(void)ignored;}
static void event(const char *name,const BridgeResponse *r){
 char line[1400];int n=snprintf(line,sizeof(line),"PC_WORKER {\"event\":\"%s\",\"pid\":%d,\"backend\":\"pangulu\",\"status\":%d,\"pangulu_status\":%d,\"cuda_status\":%d,\"n\":%llu,\"nnz\":%llu,\"canonical_nnz\":%llu,\"analysis_seconds\":%.9g,\"factor_seconds\":%.9g,\"solve_seconds\":%.9g,\"conversion_seconds\":%.9g,\"value_update_seconds\":%.9g,\"pattern_seconds\":%.9g,\"release_seconds\":%.9g,\"free_before_bytes\":%llu,\"free_bytes\":%llu}\n",name,getpid(),r->status,r->pangulu_status,r->cuda_status,(unsigned long long)r->n,(unsigned long long)r->nnz,(unsigned long long)r->canonical_nnz,r->analysis_seconds,r->factor_seconds,r->solve_seconds,r->conversion_seconds,r->value_update_seconds,r->pattern_seconds,r->release_seconds,(unsigned long long)r->free_before_bytes,(unsigned long long)r->free_bytes);if(n>0)write_log(line,(size_t)n<sizeof(line)?(size_t)n:sizeof(line)-1);
}

#else
#define event(...) ((void)0)
#endif
typedef struct {
 uint64_t handle_create_calls,handles_created,handle_destroy_calls;
 uint64_t analysis_calls,numeric_calls,refactor_calls,solve_calls,phase_failures,handle_reanalyses;
 uint64_t vector_create_calls,vector_destroy_calls,cleanup_errors;
 uint64_t device_allocations,device_frees,device_bytes_live,device_bytes_peak;
 uint64_t matrix_ipc_opens,matrix_ipc_closes,dense_ipc_opens,dense_ipc_closes;
} BackendCounts;
struct PanguBackend {
 pangulu_handle_t h;pangulu_matrix_t A;uint64_t n,nnz,*ptr;int32_t *idx;void *val;size_t item;
 int analyzed,factored,value_type,numeric_count,device,ordering;void *db,*dx;size_t dense_bytes;
 uint64_t epoch,input_n,input_nnz,*csr_to_canonical;
 int input_type,input_device,matrix_type,matrix_view;
 uint64_t union_anchor_nnz;
 int device_matrix;void *device_ptr,*device_idx,*device_val;
 void *ipc_matrix,*ipc_dense;unsigned char matrix_ipc[64],dense_ipc[64];
 uint64_t value_epoch;
 /* Created with the owning cuDSS data object, consumed by its first analysis.
  * It has no matrix, factorization, or borrowed input attached. */
 pangulu_handle_t ready_handle;
 pangulu_vector_t B,X;void *vector_b,*vector_x;uint64_t vector_n,vector_nrhs;int vector_type;
 BackendCounts counts;uint64_t data_id;int client_pid;
 int reuse_enabled,native_failed;
};
typedef struct PanguBackend State;

static void release_vectors(State*s){
 if(s->B){s->counts.vector_destroy_calls++;if(pangulu_vector_destroy(s->B))s->counts.cleanup_errors++;}
 if(s->X){s->counts.vector_destroy_calls++;if(pangulu_vector_destroy(s->X))s->counts.cleanup_errors++;}
 s->B=s->X=NULL;s->vector_b=s->vector_x=NULL;s->vector_n=s->vector_nrhs=0;
}
static void destroy_handle(State*s,pangulu_handle_t handle){if(handle){s->counts.handle_destroy_calls++;if(pangulu_destroy(handle))s->counts.cleanup_errors++;}}
static void free_buffer(State*s,void*p,size_t bytes){if(p){if(cudaFree(p)==cudaSuccess){s->counts.device_frees++;s->counts.device_bytes_live-=bytes;}else s->counts.cleanup_errors++;}}
static int allocate_buffer(State*s,void**p,size_t bytes){
 cudaError_t error=cudaMalloc(p,bytes);if(error)return (int)error;
 s->counts.device_allocations++;s->counts.device_bytes_live+=bytes;
 if(s->counts.device_bytes_live>s->counts.device_bytes_peak)s->counts.device_bytes_peak=s->counts.device_bytes_live;return 0;
}
static void close_matrix_ipc(State*s){if(s->ipc_matrix){if(cudaIpcCloseMemHandle(s->ipc_matrix)==cudaSuccess)s->counts.matrix_ipc_closes++;else s->counts.cleanup_errors++;s->ipc_matrix=NULL;}}
static int close_dense_ipc(State*s){if(s->ipc_dense){release_vectors(s);cudaError_t error=cudaIpcCloseMemHandle(s->ipc_dense);if(error==cudaSuccess)s->counts.dense_ipc_closes++;else s->counts.cleanup_errors++;s->ipc_dense=NULL;return (int)error;}return 0;}
static int vectors(State*s,void*b,void*x,uint64_t nrhs,BridgeResponse*r){
 if(s->B&&s->X&&s->vector_b==b&&s->vector_x==x&&s->vector_n==s->n&&s->vector_nrhs==nrhs&&s->vector_type==s->value_type)return 0;
 release_vectors(s);s->counts.vector_create_calls++;
 r->pangulu_status=(int)pangulu_multiple_vector_create(&s->B,(int64_t)s->n,(int64_t)nrhs,(int64_t)s->n,b,s->value_type==5?PANGULU_C_64F:PANGULU_R_64F,PANGULU_MEMORY_DEVICE);
 if(!r->pangulu_status){s->counts.vector_create_calls++;r->pangulu_status=(int)pangulu_multiple_vector_create(&s->X,(int64_t)s->n,(int64_t)nrhs,(int64_t)s->n,x,s->value_type==5?PANGULU_C_64F:PANGULU_R_64F,PANGULU_MEMORY_DEVICE);}
 if(r->pangulu_status){release_vectors(s);return -1;}
 s->vector_b=b;s->vector_x=x;s->vector_n=s->n;s->vector_nrhs=nrhs;s->vector_type=s->value_type;return 0;
}

/* End the previous matrix use before its storage is replaced. A retained
 * handle gets a complete new ANALYSIS before any numeric call. */
static void release_analysis(State *s,int keep_handle){
 if(s->A&&pangulu_matrix_destroy(s->A))s->counts.cleanup_errors++;s->A=NULL;
 if(!keep_handle){release_vectors(s);destroy_handle(s,s->h);s->h=NULL;s->reuse_enabled=0;s->native_failed=0;}
 s->analyzed=0;s->factored=0;s->numeric_count=0;
}
/* Public ANALYSIS replaces a handle's prior structure. Independent SDK
 * experiments cover repeated L2 analysis with changed shape/triangle and
 * freed old inputs. Keep the handle only within its device/scalar/type policy;
 * failures and changes of operation still use a fresh native handle. */
static int can_reanalyze(const State*s,const BridgeRequest*q){
 return s->h&&!s->native_failed&&s->reuse_enabled&&q->reuse&&s->device==q->device&&
  s->value_type==(int)q->value_type&&s->matrix_type==q->matrix_type;
}
static void clear_matrix(State*s,int keep_handle){
 release_analysis(s,keep_handle);free(s->ptr);free(s->idx);free(s->val);free(s->csr_to_canonical);
 s->ptr=NULL;s->idx=NULL;s->val=NULL;s->csr_to_canonical=NULL;
 close_matrix_ipc(s);s->device_matrix=0;s->device_ptr=s->device_idx=s->device_val=NULL;
 s->n=s->nnz=s->epoch=s->value_epoch=s->input_n=s->input_nnz=s->union_anchor_nnz=0;
}
static void clear(State *s){
 clear_matrix(s,0);
 destroy_handle(s,s->ready_handle);
 free_buffer(s,s->db,s->dense_bytes);free_buffer(s,s->dx,s->dense_bytes);
 close_dense_ipc(s);
 BackendCounts counts=s->counts;uint64_t data_id=s->data_id;int client_pid=s->client_pid;
 memset(s,0,sizeof(*s));
 s->counts=counts;s->data_id=data_id;s->client_pid=client_pid;
}
static int union_enabled(void){return 0;}
static uint64_t union_limit(uint64_t anchor){
 uint64_t limit=anchor+anchor/20; /* bounded default: at most 5% extra positions */
 const char*p=getenv("PGL_BRIDGE_UNION_MAX_NNZ");if(p&&*p){char*end=NULL;unsigned long long x=strtoull(p,&end,10);if(end&&!*end&&x>0)limit=(uint64_t)x;}
 return limit>INT32_MAX?INT32_MAX:limit;
}
static uint64_t count_new_positions(uint64_t n,const uint64_t*oldp,const int32_t*oldi,const uint64_t*p,const int32_t*i){
 uint64_t added=0;for(uint64_t col=0;col<n;col++){uint64_t old=oldp[col];for(uint64_t k=p[col];k<p[col+1];k++){while(old<oldp[col+1]&&oldi[old]<i[k])old++;if(old==oldp[col+1]||oldi[old]!=i[k])added++;}}return added;
}
static int values_equal(const State*s,const BridgeRequest*q,const void*v){
 if(!s->val||!s->csr_to_canonical)return 0;
 for(uint64_t k=0;k<q->nnz;k++)if(memcmp((const char*)s->val+s->csr_to_canonical[k]*s->item,(const char*)v+k*s->item,s->item))return 0;
 return 1;
}
static void scatter_values(State*s,const BridgeRequest*q,const void*v){
 if(s->nnz!=q->nnz)memset(s->val,0,(size_t)s->nnz*s->item);
 for(uint64_t k=0;k<q->nnz;k++)memcpy((char*)s->val+s->csr_to_canonical[k]*s->item,(const char*)v+k*s->item,s->item);
}
static int phase(State*s,pangulu_phase_t p,double*seconds,BridgeResponse*r,pangulu_vector_t x,pangulu_vector_t b){
 if(p==PANGULU_PHASE_ANALYSIS)s->counts.analysis_calls++;
 else if(p==PANGULU_PHASE_NUMERIC_FACTORISATION)s->counts.numeric_calls++;
 else if(p==PANGULU_PHASE_REFACTORISATION)s->counts.refactor_calls++;
 else if(p==PANGULU_PHASE_SOLVE)s->counts.solve_calls++;
 PGL_DETAIL(uint64_t begin=pgl_profile_ns();)
 r->pangulu_status=(int)pangulu_execute(s->h,p,s->A,x,b);
 PGL_DETAIL(uint64_t returned=pgl_profile_ns();)
 r->cuda_status=(int)cudaDeviceSynchronize();
#if PGL_PROFILE
 uint64_t end=pgl_profile_ns();*seconds+=(end-begin)*1e-9;
 fprintf(stderr,"PGL_WORKER_PHASE {\"epoch\":%llu,\"phase\":%d,\"begin_ns\":%llu,\"end_ns\":%llu,\"api_seconds\":%.9g,\"completion_sync_seconds\":%.9g,\"pangulu_status\":%d,\"cuda_status\":%d}\n",(unsigned long long)s->epoch,(int)p,(unsigned long long)begin,(unsigned long long)end,(returned-begin)*1e-9,(end-returned)*1e-9,r->pangulu_status,r->cuda_status);
#else
 (void)seconds;
#endif
 if(r->pangulu_status||r->cuda_status){s->counts.phase_failures++;s->native_failed=1;return -1;}return 0;
}
static int create_handle(State*s,pangulu_handle_t *handle,const BridgeRequest*q,BridgeResponse*r){
 PGL_DETAIL(uint64_t begin=pgl_profile_ns();)
 int gpu=0;const char*g=getenv("PC_LICENSED_GPU_INDEX");if(g)gpu=atoi(g);
 s->counts.handle_create_calls++;r->pangulu_status=(int)pangulu_create(gpu,handle);
 if(!r->pangulu_status)s->counts.handles_created++;
#if PGL_PROFILE
 uint64_t end=pgl_profile_ns();
 fprintf(stderr,"PGL_WORKER_INITIALIZE {\"command\":%llu,\"begin_ns\":%llu,\"end_ns\":%llu,\"create_seconds\":%.9g,\"pangulu_status\":%d}\n",(unsigned long long)q->command,(unsigned long long)begin,(unsigned long long)end,(end-begin)*1e-9,r->pangulu_status);
#endif
 if(r->pangulu_status){char message[2048]={0};int status=pangulu_license_status(message,sizeof(message)-1);fprintf(stderr,"PGL_LICENSE_ERROR status=%d message=%s\n",status,message);return -1;}
 int device=-1;r->cuda_status=(int)cudaGetDevice(&device);
 if(r->cuda_status){r->status=5;return -1;}
 if(device!=q->device){r->status=4;return -1;}return 0;
}
static int initialize_analysis(State*s,const BridgeRequest*q,BridgeResponse*r){
 int retained=s->h!=NULL;
 if(!s->h){
  if(s->ready_handle){s->h=s->ready_handle;s->ready_handle=NULL;}
  else if(create_handle(s,&s->h,q,r))return -1;
  s->device=q->device;
  int ordering=1;const char*o=getenv("PC_REORDERING");if(o&&*o){char*end=NULL;long x=strtol(o,&end,10);if(!end||*end||(x!=0&&x!=1)){r->status=3;return -1;}ordering=(int)x;}
  s->ordering=ordering;
  r->pangulu_status=(int)pangulu_config_set(s->h,PANGULU_REORDERING_METHOD,ordering?L1:L0);if(r->pangulu_status)return -1;
  r->pangulu_status=(int)pangulu_config_set(s->h,PANGULU_REUSE_LEVEL,q->reuse?L2:L1);if(r->pangulu_status)return -1;
  s->reuse_enabled=q->reuse;
 }
 pangulu_matrix_type_t types[]={PANGULU_MATRIX_GENERAL,PANGULU_MATRIX_SYMMETRIC,PANGULU_MATRIX_HERMITIAN,PANGULU_MATRIX_SPD,PANGULU_MATRIX_HPD};
 r->pangulu_status=(int)pangulu_matrix_create_sym(&s->A,
  s->device_matrix?PANGULU_MATRIX_LAYOUT_CSR:PANGULU_MATRIX_LAYOUT_CSC,
  (int64_t)s->n,(int64_t)s->n,(int64_t)s->nnz,1,
  s->device_matrix?s->device_ptr:s->ptr,s->device_matrix?s->device_idx:s->idx,
  s->device_matrix?s->device_val:s->val,s->device_matrix?PANGULU_R_32I:PANGULU_R_64I,
  PANGULU_R_32I,s->value_type==5?PANGULU_C_64F:PANGULU_R_64F,
  types[q->matrix_type],(pangulu_matrix_view_t)q->matrix_view,
  s->device_matrix?PANGULU_MEMORY_DEVICE:PANGULU_MEMORY_HOST);if(r->pangulu_status){s->native_failed=1;return -1;}
 if(retained)s->counts.handle_reanalyses++;
 int failed=phase(s,PANGULU_PHASE_ANALYSIS,&r->analysis_seconds,r,NULL,NULL);
 if(failed)return -1;
 s->analyzed=1;s->factored=0;s->numeric_count=0;return 0;
}
/* A failed numeric call is returned directly. No input backup or fresh retry. */
static int factor(State*s,BridgeResponse*r){
 pangulu_phase_t requested=s->numeric_count?PANGULU_PHASE_REFACTORISATION:PANGULU_PHASE_NUMERIC_FACTORISATION;
 s->factored=0;
 if(phase(s,requested,&r->factor_seconds,r,NULL,NULL))return -1;
 s->factored=1;s->numeric_count++;return 0;
}
#if PGL_PROFILE
static void pattern_event(const State*s,const BridgeRequest*q,const BridgeResponse*r,int epoch_hit,int backend_reused,int grew,int cap_reset,uint64_t added){
 if(s->device_matrix){
  char b[1000];int z=snprintf(b,sizeof(b),"PGL_BRIDGE_PATTERN {\"pid\":%d,\"command\":%llu,\"structure_epoch\":%llu,\"value_epoch\":%llu,\"policy\":\"strict_device\",\"transport\":\"%s\",\"reuse_requested\":%d,\"reordering_method\":%d,\"exact_pattern_match\":%s,\"analysis_state_reused\":%s,\"n\":%llu,\"input_nnz\":%llu,\"canonical_nnz\":%llu,\"release_seconds\":%.9g}\n",getpid(),(unsigned long long)q->command,(unsigned long long)q->structure_epoch,(unsigned long long)q->value_epoch,q->memory_space==BR_MEMORY_IPC?"cuda_ipc":"device",q->reuse,s->ordering,epoch_hit?"true":"false",backend_reused?"true":"false",(unsigned long long)s->n,(unsigned long long)q->nnz,(unsigned long long)s->nnz,r->release_seconds);if(z>0)write_log(b,(size_t)z<sizeof(b)?(size_t)z:sizeof(b)-1);return;
 }
 char b[1200];int z=snprintf(b,sizeof(b),"PGL_BRIDGE_PATTERN {\"pid\":%d,\"command\":%llu,\"structure_epoch\":%llu,\"policy\":\"%s\",\"reuse_requested\":%d,\"reordering_method\":%d,\"conversion_cache_hit\":%s,\"analysis_state_reused\":%s,\"pattern_grew\":%s,\"union_cap_reset\":%s,\"n\":%llu,\"input_nnz\":%llu,\"canonical_nnz\":%llu,\"added_positions\":%llu,\"padding_positions\":%llu,\"conversion_seconds\":%.9g,\"pattern_seconds\":%.9g,\"value_update_seconds\":%.9g,\"release_seconds\":%.9g}\n",getpid(),(unsigned long long)q->command,(unsigned long long)q->structure_epoch,union_enabled()?"union":"strict",q->reuse,s->ordering,epoch_hit?"true":"false",backend_reused?"true":"false",grew?"true":"false",cap_reset?"true":"false",(unsigned long long)s->n,(unsigned long long)q->nnz,(unsigned long long)s->nnz,(unsigned long long)added,(unsigned long long)(s->nnz-q->nnz),r->conversion_seconds,r->pattern_seconds,r->value_update_seconds,r->release_seconds);if(z>0)write_log(b,(size_t)z<sizeof(b)?(size_t)z:sizeof(b)-1);
}

#else
#define pattern_event(...) ((void)0)
#endif
static int load_device_matrix(State*s,const BridgeRequest*q,BridgeResponse*r){
 double t=now();void *memory=NULL;
 if(q->memory_space==BR_MEMORY_IPC){
  if(!s->ipc_matrix||memcmp(s->matrix_ipc,q->matrix_ipc,sizeof(s->matrix_ipc))){
   clear_matrix(s,can_reanalyze(s,q));cudaIpcMemHandle_t handle;memcpy(&handle,q->matrix_ipc,sizeof(handle));
   r->cuda_status=(int)cudaIpcOpenMemHandle(&s->ipc_matrix,handle,cudaIpcMemLazyEnablePeerAccess);
   if(r->cuda_status){r->status=5;return -1;}
   s->counts.matrix_ipc_opens++;
   memcpy(s->matrix_ipc,q->matrix_ipc,sizeof(s->matrix_ipc));
  }
  memory=s->ipc_matrix;
 }else memory=(void*)(uintptr_t)q->matrix_address;
 int same=s->device_matrix&&s->epoch==q->structure_epoch&&s->n==q->n&&
  s->nnz==q->nnz&&s->value_type==(int)q->value_type&&s->device==q->device&&
  s->matrix_type==q->matrix_type&&s->matrix_view==q->matrix_view&&
  s->device_ptr==(char*)memory+q->ptr_offset&&s->device_idx==(char*)memory+q->idx_offset&&
  s->device_val==(char*)memory+q->val_offset;
 int reused=s->analyzed&&!s->native_failed&&same&&s->reuse_enabled==q->reuse&&(q->reuse||(!s->factored&&s->value_epoch==q->value_epoch));
 if(!reused){release_analysis(s,can_reanalyze(s,q));PGL_TIME_ADD(r->release_seconds,t);}
 /* HOST arrays are never aliases of device/IPC allocations. */
 if(!s->device_matrix){free(s->ptr);free(s->idx);free(s->val);free(s->csr_to_canonical);s->ptr=NULL;s->idx=NULL;s->val=NULL;s->csr_to_canonical=NULL;}
 s->device_matrix=1;s->n=q->n;s->nnz=q->nnz;s->item=q->value_type==5?16:8;
 s->value_type=(int)q->value_type;s->device=q->device;s->matrix_type=q->matrix_type;s->matrix_view=q->matrix_view;
 s->epoch=q->structure_epoch;s->value_epoch=q->value_epoch;s->factored=0;
 s->device_ptr=(char*)memory+q->ptr_offset;s->device_idx=(char*)memory+q->idx_offset;
 s->device_val=(char*)memory+q->val_offset;r->canonical_nnz=s->nnz;
 int result=reused?1:initialize_analysis(s,q,r);pattern_event(s,q,r,same,reused,!same,0,same?0:q->nnz);return result;
}
static int load_matrix(State*s,const BridgeRequest*q,const void*memory,BridgeResponse*r){
 const uint64_t*rp=(const uint64_t*)((const char*)memory+q->ptr_offset);const int32_t*ci=(const int32_t*)((const char*)memory+q->idx_offset);const void*v=(const char*)memory+q->val_offset;
 size_t item=q->value_type==5?16:8;double t=now();
 if(union_enabled()&&!q->reuse){r->status=4;return -1;} /* explicit L2 required */
 int epoch_hit=q->structure_epoch&&s->csr_to_canonical&&s->epoch==q->structure_epoch&&s->input_n==q->n&&s->input_nnz==q->nnz&&s->input_type==(int)q->value_type&&s->input_device==q->device;
 if(epoch_hit){
  int reuse=s->analyzed&&!s->native_failed&&s->reuse_enabled==q->reuse&&(q->reuse||(!s->factored&&values_equal(s,q,v)));
  if(!reuse){t=now();release_analysis(s,can_reanalyze(s,q));PGL_TIME_ADD(r->release_seconds,t);}
  t=now();scatter_values(s,q,v);PGL_TIME_ADD(r->value_update_seconds,t);s->factored=0;r->canonical_nnz=s->nnz;
  int result=reuse?1:initialize_analysis(s,q,r);pattern_event(s,q,r,1,reuse,0,0,0);return result;
 }
 t=now();uint64_t*ptr=calloc((size_t)q->n+1,8);int32_t*idx=malloc((size_t)q->nnz*4);uint64_t*to_csc=malloc((size_t)q->nnz*8),*next=malloc((size_t)q->n*8);
 if(!ptr||!idx||!to_csc||!next){free(ptr);free(idx);free(to_csc);free(next);r->status=2;return -1;}
 if(rp[0]||rp[q->n]!=q->nnz){r->status=3;goto bad;}
 for(uint64_t row=0;row<q->n;row++){if(rp[row]>rp[row+1]||rp[row+1]>q->nnz){r->status=3;goto bad;}for(uint64_t k=rp[row];k<rp[row+1];k++){if(ci[k]<0||(uint64_t)ci[k]>=q->n){r->status=3;goto bad;}ptr[(uint64_t)ci[k]+1]++;}}
 for(uint64_t col=0;col<q->n;col++)ptr[col+1]+=ptr[col];memcpy(next,ptr,(size_t)q->n*8);
 for(uint64_t row=0;row<q->n;row++)for(uint64_t k=rp[row];k<rp[row+1];k++){uint64_t z=next[ci[k]]++;idx[z]=(int32_t)row;to_csc[k]=z;}
 free(next);next=NULL;PGL_TIME_ADD(r->conversion_seconds,t);
 if(pc_validate(q->n,ptr,idx)){r->status=3;goto bad;}
 {
  t=now();int compatible=s->n==q->n&&s->value_type==(int)q->value_type&&s->device==q->device;
  PcUnion u={0};int cap_reset=0;
  if(union_enabled()&&compatible&&s->ptr){
   int status=pc_pattern_union_validated(q->n,s->ptr,s->idx,ptr,idx,union_limit(s->union_anchor_nnz),&u);
   if(status==PC_TOO_LARGE){cap_reset=1;status=pc_pattern_union_validated(q->n,NULL,NULL,ptr,idx,INT32_MAX,&u);}
   if(status){r->status=status==PC_ALLOC_FAILED?2:3;goto bad;}
  }else{
   int same=compatible&&s->ptr&&s->nnz==q->nnz&&!memcmp(s->ptr,ptr,((size_t)q->n+1)*8)&&!memcmp(s->idx,idx,(size_t)q->nnz*4);
   if(same){u.n=q->n;u.nnz=q->nnz;u.input_nnz=q->nnz;u.ptr=s->ptr;u.idx=s->idx;}
   else{u.n=q->n;u.nnz=q->nnz;u.input_nnz=q->nnz;u.added_positions=compatible&&s->ptr?count_new_positions(q->n,s->ptr,s->idx,ptr,idx):q->nnz;u.grew=1;u.own_arrays=1;u.ptr=ptr;u.idx=idx;ptr=NULL;idx=NULL;}
  }
  /* Strict mode's canonical CSC is already identical to the conversion.
   * Only an actual union needs a second nnz-sized translation map. */
  if(union_enabled()){
  const uint64_t*np=ptr?ptr:u.ptr;const int32_t*ni=idx?idx:u.idx;
  uint64_t*to_union=malloc((size_t)q->nnz*8);
  if(!to_union){pc_release(&u);r->status=2;goto bad;}
  for(uint64_t col=0;col<q->n;col++){
   uint64_t z=u.ptr[col];for(uint64_t k=np[col];k<np[col+1];k++){while(z<u.ptr[col+1]&&u.idx[z]<ni[k])z++;if(z==u.ptr[col+1]||u.idx[z]!=ni[k]){free(to_union);pc_release(&u);r->status=3;goto bad;}to_union[k]=z;}
  }
  for(uint64_t k=0;k<q->nnz;k++)to_csc[k]=to_union[to_csc[k]];free(to_union);
  }
  PGL_TIME_ADD(r->pattern_seconds,t);
  int same_pattern=compatible&&!u.grew;
  int same_values=same_pattern;
  if(same_values&&!q->reuse&&!s->factored){for(uint64_t k=0;k<q->nnz;k++)if(memcmp((const char*)s->val+to_csc[k]*item,(const char*)v+k*item,item)){same_values=0;break;}}
  int reused=s->analyzed&&!s->native_failed&&s->reuse_enabled==q->reuse&&same_pattern&&(q->reuse||(!s->factored&&same_values));
  if(!reused){t=now();release_analysis(s,can_reanalyze(s,q));PGL_TIME_ADD(r->release_seconds,t);}
  if(u.grew){
   void*newval=calloc((size_t)u.nnz,item);if(!newval){pc_release(&u);r->status=2;goto bad;}
   free(s->ptr);free(s->idx);free(s->val);s->ptr=(uint64_t*)u.ptr;s->idx=(int32_t*)u.idx;s->val=newval;s->nnz=u.nnz;s->n=q->n;s->item=item;s->value_type=(int)q->value_type;s->device=q->device;u.own_arrays=0;
   if(!compatible||!s->union_anchor_nnz||cap_reset)s->union_anchor_nnz=q->nnz;
  }
  free(s->csr_to_canonical);s->csr_to_canonical=to_csc;to_csc=NULL;s->epoch=q->structure_epoch;s->input_n=q->n;s->input_nnz=q->nnz;s->input_type=(int)q->value_type;s->input_device=q->device;
  t=now();scatter_values(s,q,v);PGL_TIME_ADD(r->value_update_seconds,t);s->factored=0;r->canonical_nnz=s->nnz;
  uint64_t added=u.added_positions;int grew=u.grew;pc_release(&u);free(ptr);free(idx);
  int result=reused?1:initialize_analysis(s,q,r);pattern_event(s,q,r,0,reused,grew,cap_reset,added);return result;
 }
bad:free(ptr);free(idx);free(to_csc);free(next);return -1;
}

static int probe_ipc(const BridgeRequest*q,BridgeResponse*r){
  r->cuda_status=(int)cudaSetDevice(q->device);if(r->cuda_status){r->status=5;return r->status;}
  cudaIpcMemHandle_t handle;memcpy(&handle,q->matrix_ipc,sizeof(handle));void *probe=NULL;
  cudaError_t error=cudaIpcOpenMemHandle(&probe,handle,cudaIpcMemLazyEnablePeerAccess);
  if(error){
   r->cuda_status=(int)error;
   r->status=(error==cudaErrorInvalidResourceHandle||error==cudaErrorNotSupported)?BR_TRANSPORT_UNAVAILABLE:error==cudaErrorMemoryAllocation?2:5;
   cudaGetLastError();return r->status;
  }
  uint64_t value=0;error=cudaMemcpy(&value,probe,sizeof(value),cudaMemcpyDeviceToHost);
  cudaError_t closed=cudaIpcCloseMemHandle(probe);if(!error)error=closed;
  r->cuda_status=(int)error;if(error)r->status=5;else if(value!=PGL_BRIDGE_MAGIC)r->status=3;
  return r->status;
}

PanguBackend *pc_backend_create(void){return calloc(1,sizeof(State));}
void pc_backend_destroy(PanguBackend *state){if(state){
 clear(state);const BackendCounts*c=&state->counts;
 fprintf(stderr,"PGL_BACKEND_SUMMARY {\"pid\":%d,\"client_pid\":%d,\"data_id\":%llu,\"handle_create_calls\":%llu,\"handles_created\":%llu,\"handle_destroy_calls\":%llu,\"analysis_calls\":%llu,\"handle_reanalyses\":%llu,\"numeric_calls\":%llu,\"refactor_calls\":%llu,\"solve_calls\":%llu,\"phase_failures\":%llu,\"vector_create_calls\":%llu,\"vector_destroy_calls\":%llu,\"device_allocations\":%llu,\"device_frees\":%llu,\"backend_buffer_bytes_live\":%llu,\"backend_buffer_bytes_peak\":%llu,\"matrix_ipc_opens\":%llu,\"matrix_ipc_closes\":%llu,\"dense_ipc_opens\":%llu,\"dense_ipc_closes\":%llu,\"cleanup_errors\":%llu}\n",getpid(),state->client_pid,(unsigned long long)state->data_id,(unsigned long long)c->handle_create_calls,(unsigned long long)c->handles_created,(unsigned long long)c->handle_destroy_calls,(unsigned long long)c->analysis_calls,(unsigned long long)c->handle_reanalyses,(unsigned long long)c->numeric_calls,(unsigned long long)c->refactor_calls,(unsigned long long)c->solve_calls,(unsigned long long)c->phase_failures,(unsigned long long)c->vector_create_calls,(unsigned long long)c->vector_destroy_calls,(unsigned long long)c->device_allocations,(unsigned long long)c->device_frees,(unsigned long long)c->device_bytes_live,(unsigned long long)c->device_bytes_peak,(unsigned long long)c->matrix_ipc_opens,(unsigned long long)c->matrix_ipc_closes,(unsigned long long)c->dense_ipc_opens,(unsigned long long)c->dense_ipc_closes,(unsigned long long)c->cleanup_errors);
 free(state);
}}
int pc_backend_dispatch(PanguBackend *s,const BridgeRequest *q,BridgeResponse *r,void *memory){
 memset(r,0,sizeof(*r));r->magic=PGL_BRIDGE_MAGIC;
 if(!s||!q||q->magic!=PGL_BRIDGE_MAGIC){r->status=3;return r->status;}
 s->data_id=q->data_id;s->client_pid=q->client_pid;
 r->n=q->n;r->nnz=q->nnz;
 if(q->command==BR_CLOSE){clear(s);return 0;}
 if(q->command==BR_INITIALIZE){
  if(s->h||s->ready_handle){r->status=3;return r->status;}
  if(create_handle(s,&s->ready_handle,q,r)&&!r->status)r->status=5;
  r->stage=BR_INITIALIZE;return r->status;
 }
 if(q->command==BR_PROBE_IPC){
  r->stage=BR_PROBE_IPC;
  if((!s->h&&!s->ready_handle)||q->memory_space!=BR_MEMORY_IPC){r->status=3;return r->status;}
  return probe_ipc(q,r);
 }

  if(q->n==0||q->n>INT32_MAX||q->nnz==0||q->nnz>INT32_MAX||(q->nrhs<1||q->nrhs>65536)||q->matrix_type<0||q->matrix_type>4||q->matrix_view<0||q->matrix_view>2||(q->value_type!=1&&q->value_type!=5)||q->memory_space<BR_MEMORY_HOST||q->memory_space>BR_MEMORY_IPC){r->status=3;goto done;}
  uint64_t element=q->value_type==5?16:8,limit=UINT64_C(20)*1024*1024*1024;
  uint64_t dense=q->n*q->nrhs*element;
  if(q->memory_space==BR_MEMORY_HOST){
   if(!memory||q->total_bytes>limit||q->ptr_offset>q->total_bytes||(q->n+1)*8>q->total_bytes-q->ptr_offset||q->idx_offset>q->total_bytes||q->nnz*4>q->total_bytes-q->idx_offset||q->val_offset>q->total_bytes||q->nnz*element>q->total_bytes-q->val_offset||q->b_offset>q->total_bytes||dense>q->total_bytes-q->b_offset||q->x_offset>q->total_bytes||dense>q->total_bytes-q->x_offset){r->status=3;goto done;}
  }else{
   if(q->matrix_bytes>limit||q->ptr_offset>q->matrix_bytes||(q->n+1)*4>q->matrix_bytes-q->ptr_offset||q->idx_offset>q->matrix_bytes||q->nnz*4>q->matrix_bytes-q->idx_offset||q->val_offset>q->matrix_bytes||q->nnz*element>q->matrix_bytes-q->val_offset||(q->memory_space==BR_MEMORY_DEVICE&&!q->matrix_address)){r->status=3;goto done;}
   if(q->command==BR_SOLVE&&(q->dense_bytes>limit||q->b_offset>q->dense_bytes||dense>q->dense_bytes-q->b_offset||q->x_offset>q->dense_bytes||dense>q->dense_bytes-q->x_offset||(q->memory_space==BR_MEMORY_DEVICE&&!q->dense_address))){r->status=3;goto done;}
   r->cuda_status=(int)cudaSetDevice(q->device);if(r->cuda_status){r->status=5;goto done;}
  }
 PGL_DETAIL({size_t f=0,t=0;if(cudaMemGetInfo(&f,&t)==cudaSuccess)r->free_before_bytes=f;})
  if(q->command==BR_ANALYZE||q->command==BR_FACTOR){
   if(q->memory_space==BR_MEMORY_HOST&&s->device_matrix)clear_matrix(s,can_reanalyze(s,q));
   if(q->memory_space==BR_MEMORY_HOST&&s->h&&(s->matrix_type!=q->matrix_type||s->matrix_view!=q->matrix_view))clear_matrix(s,can_reanalyze(s,q));
   if(q->memory_space==BR_MEMORY_HOST){
   s->matrix_type=q->matrix_type;s->matrix_view=q->matrix_view;
   }
   int reused=q->memory_space==BR_MEMORY_HOST?load_matrix(s,q,memory,r):load_device_matrix(s,q,r);if(reused<0){if(!r->status)r->status=5;goto done;}
   if(q->command==BR_FACTOR){if(factor(s,r)){if(!r->status)r->status=5;goto done;}}
  }else if(q->command==BR_SOLVE){
   if(!s->factored||s->n!=q->n||s->value_type!=(int)q->value_type){r->status=1;goto done;}
   if(q->memory_space!=BR_MEMORY_HOST){
    if(!s->device_matrix||s->epoch!=q->structure_epoch){r->status=1;goto done;}
    void *base=(void*)(uintptr_t)q->dense_address;
    if(q->memory_space==BR_MEMORY_IPC){
     if(!s->ipc_dense||memcmp(s->dense_ipc,q->dense_ipc,sizeof(s->dense_ipc))){
      r->cuda_status=close_dense_ipc(s);if(r->cuda_status){r->status=5;goto done;}
      cudaIpcMemHandle_t handle;memcpy(&handle,q->dense_ipc,sizeof(handle));
      r->cuda_status=(int)cudaIpcOpenMemHandle(&s->ipc_dense,handle,cudaIpcMemLazyEnablePeerAccess);
      if(r->cuda_status){r->status=5;goto done;}s->counts.dense_ipc_opens++;memcpy(s->dense_ipc,q->dense_ipc,sizeof(s->dense_ipc));
     }
     base=s->ipc_dense;
    }
    if(!vectors(s,(char*)base+q->b_offset,(char*)base+q->x_offset,q->nrhs,r))phase(s,PANGULU_PHASE_SOLVE,&r->solve_seconds,r,s->X,s->B);
    if(r->pangulu_status||r->cuda_status)r->status=5;
    goto done;
   }
   size_t bytes=(size_t)s->n*s->item*q->nrhs;
   if(!s->db||!s->dx||bytes>s->dense_bytes){
    release_vectors(s);free_buffer(s,s->db,s->dense_bytes);free_buffer(s,s->dx,s->dense_bytes);s->db=s->dx=NULL;s->dense_bytes=bytes;
    r->cuda_status=allocate_buffer(s,&s->db,bytes);if(!r->cuda_status)r->cuda_status=allocate_buffer(s,&s->dx,bytes);if(r->cuda_status){r->status=2;goto done;}
   }
   r->cuda_status=(int)cudaMemcpy(s->db,(char*)memory+q->b_offset,bytes,cudaMemcpyHostToDevice);if(!r->cuda_status)r->cuda_status=(int)cudaMemset(s->dx,0,bytes);if(!r->cuda_status)r->cuda_status=(int)cudaDeviceSynchronize();if(r->cuda_status){r->status=5;goto done;}
   if(!vectors(s,s->db,s->dx,q->nrhs,r))phase(s,PANGULU_PHASE_SOLVE,&r->solve_seconds,r,s->X,s->B);
   if(r->pangulu_status||r->cuda_status){r->status=5;goto done;}
   r->cuda_status=(int)cudaMemcpy((char*)memory+q->x_offset,s->dx,bytes,cudaMemcpyDeviceToHost);if(r->cuda_status){r->status=5;goto done;}
   const double*out=(const double*)((char*)memory+q->x_offset);for(size_t i=0;i<bytes/8;i++)if(!isfinite(out[i])){r->status=5;break;}
  }else r->status=4;
done:
  r->canonical_nnz=s->nnz;r->stage=(int32_t)q->command;PGL_DETAIL({size_t f=0,t=0;if(cudaMemGetInfo(&f,&t)==cudaSuccess){r->free_bytes=f;r->total_bytes=t;}})
  event(q->command==BR_ANALYZE?"analysis":q->command==BR_FACTOR?"factorization":"solve",r);
 return r->status;
}
