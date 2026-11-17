#include "device.h"
#include <cuda_runtime.h>

template<class I> __global__ void stage_offsets(const I *starts,const I *ends,
 int base,int64_t n,int64_t nnz,int32_t *ptr,int compare,int *status){
 for(int64_t row=blockIdx.x*(int64_t)blockDim.x+threadIdx.x;row<n;
     row+=(int64_t)blockDim.x*gridDim.x){
  int flags=0;int64_t first=starts[row],last=ends?ends[row]:starts[row+1];
  if(first<base||last<first||last>nnz+base||(row==0&&first!=base)||
     (row==n-1&&last!=nnz+base))flags|=PGL_DEVICE_INVALID;
  if(ends&&row+1<n&&(int64_t)ends[row]!=(int64_t)starts[row+1])
   flags|=PGL_DEVICE_NONCONTIGUOUS;
  if(!(flags&PGL_DEVICE_INVALID)){
   int32_t begin=(int32_t)(first-base),end=(int32_t)(last-base);
   if(compare&&ptr[row]!=begin)flags|=PGL_DEVICE_PATTERN_CHANGED;
   ptr[row]=begin;
   if(row==n-1){if(compare&&ptr[n]!=end)flags|=PGL_DEVICE_PATTERN_CHANGED;ptr[n]=end;}
  }
  if(flags&(PGL_DEVICE_INVALID|PGL_DEVICE_NONCONTIGUOUS))atomicOr(status+1,1);
  if(flags)atomicOr(status,flags);
 }
}

/* One warp per row gives coalesced CSR reads. Offsets are fully checked by the
 * preceding kernel before any index/value access (including malformed input).
 * Every normalized index and every value bit is compared with the prior owned
 * snapshot. No checksum or pointer-identity shortcut establishes equality. */
template<class I> __global__ void stage_entries(const I *indices,
 const unsigned long long *values,int base,int64_t n,int words,
 const int32_t *ptr,int32_t *idx,unsigned long long *val,int compare,int *status){
 /* This word is finalized by stage_offsets and never written here. Keep it
  * separate from flags, which other entry blocks may update concurrently. */
 if(status[1])return;
 int lane=threadIdx.x&31,flags=0;
 for(int64_t row=(blockIdx.x*(int64_t)blockDim.x+threadIdx.x)/32;row<n;
     row+=(int64_t)blockDim.x*gridDim.x/32){
  int64_t begin=ptr[row],end=ptr[row+1];
  for(int64_t k=begin+lane;k<end;k+=32){
   int64_t raw=indices[k];
   if(raw<base||raw>=n+base){flags|=PGL_DEVICE_INVALID;continue;}
   int32_t col=(int32_t)(raw-base);
   if(k>begin&&raw<=(int64_t)indices[k-1])flags|=PGL_DEVICE_UNSORTED;
   if(compare&&idx[k]!=col)flags|=PGL_DEVICE_PATTERN_CHANGED;
   idx[k]=col;
   for(int j=0;j<words;j++){
    unsigned long long v=values[k*words+j];
    if(compare&&val[k*words+j]!=v)flags|=PGL_DEVICE_VALUES_CHANGED;
    val[k*words+j]=v;
   }
  }
 }
 for(int offset=16;offset;offset/=2)flags|=__shfl_down_sync(0xffffffff,flags,offset);
 if(lane==0&&flags)atomicOr(status,flags);
}

__global__ void check_finite(const double *values,size_t n,int *status){
 for(size_t k=blockIdx.x*(size_t)blockDim.x+threadIdx.x;k<n;
     k+=(size_t)blockDim.x*gridDim.x)
  if(!isfinite(values[k]))atomicOr(status,1);
}

extern "C" int pgl_device_stage(const void *starts,const void *ends,
 const void *indices,const void *values,int index_bytes,int base,int64_t n,
 int64_t nnz,int value_bytes,int32_t *ptr,int32_t *idx,void *val,int compare,
 int *status_device,int *flags){
 cudaError_t e=cudaMemset(status_device,0,PGL_DEVICE_STATUS_WORDS*sizeof(int));if(e)return (int)e;
 int offset_blocks=(int)((n+255)/256);if(offset_blocks>4096)offset_blocks=4096;
 int entry_blocks=(int)((n+7)/8);if(entry_blocks>4096)entry_blocks=4096;
 if(index_bytes==8){
  stage_offsets<<<offset_blocks,256>>>((const int64_t*)starts,(const int64_t*)ends,
   base,n,nnz,ptr,compare,status_device);
  stage_entries<<<entry_blocks,256>>>((const int64_t*)indices,
   (const unsigned long long*)values,base,n,value_bytes/8,ptr,idx,
   (unsigned long long*)val,compare,status_device);
 }else{
  stage_offsets<<<offset_blocks,256>>>((const int32_t*)starts,(const int32_t*)ends,
   base,n,nnz,ptr,compare,status_device);
  stage_entries<<<entry_blocks,256>>>((const int32_t*)indices,
   (const unsigned long long*)values,base,n,value_bytes/8,ptr,idx,
   (unsigned long long*)val,compare,status_device);
 }
 e=cudaGetLastError();if(e)return (int)e;
 return (int)cudaMemcpy(flags,status_device,sizeof(int),cudaMemcpyDeviceToHost);
}

extern "C" int pgl_device_finite(const void *values,size_t n,
 int *status_device,int *invalid){
 cudaError_t e=cudaMemset(status_device,0,sizeof(int));if(e)return (int)e;
 int blocks=(int)((n+255)/256);if(blocks>4096)blocks=4096;
 check_finite<<<blocks,256>>>((const double*)values,n,status_device);
 e=cudaGetLastError();if(e)return (int)e;
 return (int)cudaMemcpy(invalid,status_device,sizeof(int),cudaMemcpyDeviceToHost);
}
