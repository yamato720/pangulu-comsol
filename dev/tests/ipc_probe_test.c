/* CPU-only fault injection of the production transport probe. No CUDA driver. */
#include "../../src/backend.c"
#include <assert.h>
static cudaError_t opened,copied,closed;
static int wrong_value,close_calls;
cudaError_t cudaSetDevice(int device){return device==0?cudaSuccess:cudaErrorInvalidDevice;}
cudaError_t cudaIpcOpenMemHandle(void **pointer,cudaIpcMemHandle_t handle,unsigned int flags){(void)handle;(void)flags;*pointer=(void*)1;return opened;}
cudaError_t cudaMemcpy(void *destination,const void *source,size_t bytes,enum cudaMemcpyKind kind){(void)source;(void)kind;assert(bytes==8);*(uint64_t*)destination=wrong_value?0:PGL_BRIDGE_MAGIC;return copied;}
cudaError_t cudaIpcCloseMemHandle(void *pointer){assert(pointer==(void*)1);close_calls++;return closed;}
cudaError_t cudaGetLastError(void){return cudaSuccess;}
static void run(cudaError_t open_error,cudaError_t copy_error,cudaError_t close_error,int bad,int expected){
 opened=open_error;copied=copy_error;closed=close_error;wrong_value=bad;close_calls=0;
 BridgeRequest q={0};BridgeResponse r={0};q.device=0;
 assert(probe_ipc(&q,&r)==expected);assert(close_calls==(open_error==cudaSuccess?1:0));
}
int main(void){
 run(cudaSuccess,cudaSuccess,cudaSuccess,0,0);
 run(cudaErrorInvalidResourceHandle,cudaSuccess,cudaSuccess,0,BR_TRANSPORT_UNAVAILABLE);
 run(cudaErrorNotSupported,cudaSuccess,cudaSuccess,0,BR_TRANSPORT_UNAVAILABLE);
 run(cudaErrorMemoryAllocation,cudaSuccess,cudaSuccess,0,2);
 run(cudaErrorInvalidDevice,cudaSuccess,cudaSuccess,0,5);
 run(cudaSuccess,cudaErrorInvalidValue,cudaSuccess,0,5);
 run(cudaSuccess,cudaSuccess,cudaErrorInvalidResourceHandle,0,5);
 run(cudaSuccess,cudaSuccess,cudaSuccess,1,3);
 puts("IPC_PROBE_FAULT_TEST_PASS cases=8");return 0;
}
