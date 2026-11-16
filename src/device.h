#ifndef PGL_COMSOL_DEVICE_H
#define PGL_COMSOL_DEVICE_H
#include <stdint.h>
#include <stddef.h>
#ifndef PGL_DEVICE
#define PGL_DEVICE 0
#endif
#ifdef __cplusplus
extern "C" {
#endif
enum {
 PGL_DEVICE_INVALID=1, PGL_DEVICE_UNSORTED=2,
 PGL_DEVICE_PATTERN_CHANGED=4, PGL_DEVICE_VALUES_CHANGED=8,
 PGL_DEVICE_NONCONTIGUOUS=16, PGL_DEVICE_BAD_OFFSETS=32
};
/* All source and destination pointers are device-accessible. status_device is
 * a caller-owned int; flags is host memory. A successful call synchronizes. */
int pgl_device_stage(const void *starts,const void *ends,const void *indices,
 const void *values,int index_bytes,int base,int64_t n,int64_t nnz,int value_bytes,
 int32_t *ptr,int32_t *idx,void *val,int compare,int *status_device,int *flags);
int pgl_device_finite(const void *values,size_t double_count,int *status_device,int *invalid);
#ifdef __cplusplus
}
#endif
#endif
