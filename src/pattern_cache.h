#ifndef PGL_BRIDGE_PATTERN_CACHE_H
#define PGL_BRIDGE_PATTERN_CACHE_H

/* Exact CSC pattern supersets for numerical refactorization.
 *
 * No coefficient is filtered or approximated. Values absent from the current
 * matrix receive exactly zero in a stored structural position. Indices must be
 * sorted and unique within each column. Reuse uses exact subset checks.
 *
 * Lifecycle: an expanded pattern is newly allocated. Destroy the old Pangu
 * descriptor/handle BEFORE freeing its old caller-owned arrays. When a pattern
 * is unchanged, ptr/idx below borrow the existing arrays and own_arrays=0.
 */
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>

enum pc_status {
    PC_OK=0, PC_INVALID=-1, PC_ALLOC_FAILED=-2, PC_TOO_LARGE=-3,
    PC_NOT_SUBSET=-4, PC_VALUE_ALIAS=-5
};
typedef struct {
    uint64_t n, nnz, input_nnz, added_positions, padding_positions;
    const uint64_t *ptr;
    const int32_t *idx;
    int own_arrays, grew;
} PcUnion;

static inline int pc_validate(uint64_t n,const uint64_t *p,const int32_t *i) {
    if(!n || n>INT32_MAX || !p || p[0] || p[n]>INT32_MAX || (p[n]&&!i))return PC_INVALID;
    for(uint64_t col=0;col<n;col++) {
        if(p[col]>p[col+1]||p[col+1]>p[n])return PC_INVALID;
        int32_t previous=-1;
        for(uint64_t k=p[col];k<p[col+1];k++) {
            if(i[k]<0||(uint64_t)i[k]>=n||i[k]<=previous)return PC_INVALID;
            previous=i[k];
        }
    }
    return PC_OK;
}

static inline void pc_release(PcUnion *u) {
    if(!u)return;
    if(u->own_arrays){free((void*)u->ptr);free((void*)u->idx);}
    memset(u,0,sizeof(*u));
}

/* old_p=NULL begins a new dimension/type/device cache generation. Otherwise the
 * caller must supply the pattern from that same generation and dimension n. */
/* Internal entry for already validated current CSC and immutable cached CSC. */
static inline int pc_pattern_union_validated(uint64_t n,const uint64_t *old_p,const int32_t *old_i,
                                    const uint64_t *cur_p,const int32_t *cur_i,
                                    uint64_t maximum_union_nnz,PcUnion *out) {
    if(!out)return PC_INVALID;
    memset(out,0,sizeof(*out));
    uint64_t added=0,merged=0;
    if(!old_p){added=cur_p[n];merged=added;if(merged>maximum_union_nnz)return PC_TOO_LARGE;}
    else for(uint64_t col=0;col<n;col++) {
        uint64_t a=old_p[col],b=cur_p[col];
        while(a<old_p[col+1]||b<cur_p[col+1]) {
            if(b==cur_p[col+1]||(a<old_p[col+1]&&old_i[a]<cur_i[b]))a++;
            else if(a==old_p[col+1]||cur_i[b]<old_i[a]){b++;added++;}
            else{a++;b++;}
            if(++merged>INT32_MAX||merged>maximum_union_nnz)return PC_TOO_LARGE;
        }
    }
    out->n=n;out->nnz=merged;out->input_nnz=cur_p[n];out->added_positions=added;
    out->padding_positions=merged-cur_p[n];out->grew=!old_p||added!=0;
    if(old_p && !added){out->ptr=old_p;out->idx=old_i;return PC_OK;}
    if(n+1>SIZE_MAX/sizeof(uint64_t)||merged>SIZE_MAX/sizeof(int32_t))return PC_TOO_LARGE;
    uint64_t *p=(uint64_t*)malloc((size_t)(n+1)*sizeof(*p));
    int32_t *i=merged?(int32_t*)malloc((size_t)merged*sizeof(*i)):NULL;
    if(!p||(merged&&!i)){free(p);free(i);memset(out,0,sizeof(*out));return PC_ALLOC_FAILED;}
    if(!old_p){memcpy(p,cur_p,(size_t)(n+1)*sizeof(*p));if(merged)memcpy(i,cur_i,(size_t)merged*sizeof(*i));}
    else {
        uint64_t z=0;p[0]=0;
        for(uint64_t col=0;col<n;col++) {
            uint64_t a=old_p[col],b=cur_p[col];
            while(a<old_p[col+1]||b<cur_p[col+1]) {
                if(b==cur_p[col+1]||(a<old_p[col+1]&&old_i[a]<cur_i[b]))i[z++]=old_i[a++];
                else if(a==old_p[col+1]||cur_i[b]<old_i[a])i[z++]=cur_i[b++];
                else{i[z++]=old_i[a++];b++;}
            }
            p[col+1]=z;
        }
    }
    out->ptr=p;out->idx=i;out->own_arrays=1;return PC_OK;
}

static inline int pc_pattern_union_limited(uint64_t n,const uint64_t *old_p,const int32_t *old_i,
                                    const uint64_t *cur_p,const int32_t *cur_i,
                                    uint64_t maximum_union_nnz,PcUnion *out) {
    if(!out)return PC_INVALID;
    memset(out,0,sizeof(*out));
    int status=pc_validate(n,cur_p,cur_i);if(status)return status;
    if(old_p && (status=pc_validate(n,old_p,old_i)))return status;
    return pc_pattern_union_validated(n,old_p,old_i,cur_p,cur_i,maximum_union_nnz,out);
}

static inline int pc_pattern_union(uint64_t n,const uint64_t *old_p,const int32_t *old_i,
                                    const uint64_t *cur_p,const int32_t *cur_i,PcUnion *out) {
    return pc_pattern_union_limited(n,old_p,old_i,cur_p,cur_i,INT32_MAX,out);
}

/* Scatter the current matrix into a previously verified superset. item_bytes
 * is 8 (real64) or 16 (complex128). Every current coefficient is copied bit for
 * bit. A current index outside the superset is an error, not silently dropped.
 * current_values and output_values must not overlap. */
static inline int pc_scatter_values(uint64_t n,const uint64_t *union_p,const int32_t *union_i,
                                    const uint64_t *cur_p,const int32_t *cur_i,
                                    const void *current_values,size_t item_bytes,void *output_values) {
    if(item_bytes!=8&&item_bytes!=16)return PC_INVALID;
    int status=pc_validate(n,union_p,union_i);if(status)return status;
    status=pc_validate(n,cur_p,cur_i);if(status)return status;
    if(union_p[n]>SIZE_MAX/item_bytes||cur_p[n]>SIZE_MAX/item_bytes)return PC_TOO_LARGE;
    size_t out_bytes=(size_t)union_p[n]*item_bytes,in_bytes=(size_t)cur_p[n]*item_bytes;
    if((in_bytes&&!current_values)||(out_bytes&&!output_values))return PC_INVALID;
    uintptr_t a=(uintptr_t)current_values,b=(uintptr_t)output_values;
    if(in_bytes&&out_bytes&&((a<=b&&b-a<in_bytes)||(b<a&&a-b<out_bytes)))return PC_VALUE_ALIAS;
    /* Check complete containment before modifying output; a failed subset check
     * therefore leaves any existing factorization input untouched. */
    for(uint64_t col=0;col<n;col++) {
        uint64_t u=union_p[col];
        for(uint64_t c=cur_p[col];c<cur_p[col+1];c++) {
            while(u<union_p[col+1]&&union_i[u]<cur_i[c])u++;
            if(u==union_p[col+1]||union_i[u]!=cur_i[c])return PC_NOT_SUBSET;
        }
    }
    if(out_bytes)memset(output_values,0,out_bytes);
    for(uint64_t col=0;col<n;col++) {
        uint64_t u=union_p[col];
        for(uint64_t c=cur_p[col];c<cur_p[col+1];c++) {
            while(union_i[u]<cur_i[c])u++;
            memcpy((char*)output_values+(size_t)u*item_bytes,
                   (const char*)current_values+(size_t)c*item_bytes,item_bytes);
        }
    }
    return PC_OK;
}

#endif
