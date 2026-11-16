/* CPU-only fault injection for the real bridge allocator. Link with function
 * sections and --gc-sections; no CUDA driver or numerical SDK is executed. */
#define PGL_DEVICE 1
#include "../../src/bridge.cpp"
#include <cassert>

static std::vector<size_t> requests;
static cudaError_t first_error=cudaSuccess;
static int cleared_errors=0;
extern "C" cudaError_t cudaMalloc(void **p,size_t bytes){
 requests.push_back(bytes);
 if(requests.size()==1&&first_error!=cudaSuccess)return first_error;
 *p=malloc(1);return *p?cudaSuccess:cudaErrorMemoryAllocation;
}
extern "C" cudaError_t cudaFree(void *p){free(p);return cudaSuccess;}
extern "C" cudaError_t cudaGetLastError(){cleared_errors++;return cudaSuccess;}
extern "C" cudaError_t cudaIpcGetMemHandle(cudaIpcMemHandle_t*,void*){assert(false);return cudaErrorNotSupported;}

static cudssStatus_t reserve(cudssData &d,size_t bytes){
 bool changed=false;return allocate_device(&d,&d.device_matrix,&d.device_matrix_capacity,
  bytes,&d.matrix_ipc,d.retired_matrix,changed,d.counts.matrix_allocations,true);
}
static void reset(cudaError_t error=cudaSuccess){requests.clear();first_error=error;cleared_errors=0;}
static void clean(cudssData &d){free_device(&d);assert(d.counts.device_bytes_live==0);assert(d.counts.cleanup_errors==0);}

int main(){
 setenv("PC_EXECUTION","direct",1);
 const size_t limit=(size_t)(UINT64_C(20)*1024*1024*1024);
 {
  reset();cudssData d;
  assert(reserve(d,800)==CUDSS_STATUS_SUCCESS);
  assert(requests==std::vector<size_t>{900});assert(d.device_matrix_capacity==900);
  assert(reserve(d,880)==CUDSS_STATUS_SUCCESS);assert(requests.size()==1);
  assert(reserve(d,904)==CUDSS_STATUS_SUCCESS);assert(requests.back()==1017);
  assert(d.retired_matrix.size()==1);clean(d);assert(d.counts.device_frees==2);
 }
 {
  reset(cudaErrorMemoryAllocation);cudssData d;
  assert(reserve(d,800)==CUDSS_STATUS_SUCCESS);
  assert((requests==std::vector<size_t>{900,800}));assert(cleared_errors==1);
  assert(d.device_matrix_capacity==800);assert(d.counts.matrix_reserve_oom_fallbacks==1);
  assert(d.counts.matrix_allocations==1);clean(d);
 }
 {
  reset(cudaErrorInvalidValue);cudssData d;
  assert(reserve(d,800)==CUDSS_STATUS_EXECUTION_FAILED);
  assert(requests.size()==1);assert(cleared_errors==0);
  assert(d.counts.matrix_reserve_oom_fallbacks==0);clean(d);
 }
 {
  reset();cudssData d;
  assert(reserve(d,limit-8)==CUDSS_STATUS_SUCCESS);
  assert(requests==std::vector<size_t>{limit});assert(d.device_matrix_capacity==limit);
  assert(reserve(d,limit)==CUDSS_STATUS_SUCCESS);assert(requests.size()==1);clean(d);
 }
 {
  reset(cudaErrorMemoryAllocation);cudssData d;
  assert(reserve(d,limit)==CUDSS_STATUS_ALLOC_FAILED);assert(requests.size()==1);
  assert(d.counts.matrix_reserve_oom_fallbacks==0);clean(d);
 }
 {
  reset();cudssData d;
  assert(reserve(d,limit+1)==CUDSS_STATUS_ALLOC_FAILED);assert(requests.empty());clean(d);
 }
 puts("MATRIX_CAPACITY_CPU_TEST_PASS");
}
